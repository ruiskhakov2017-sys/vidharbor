import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import Client, TransactionTestCase

from . import journal
from .deletion import revoke, clean, clean_pending, apply_journal
from .job_attempts import begin_attempt, finish_attempt
from .models import Attempt, Job, Upload, Video
from .publication import publish_video
from .storage import media_lock, original_path


class DeletionTests(TransactionTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(self.settings(MEDIA_ROOT=self.root, JOURNAL_ROOT=self.root / "authority"))
        for folder in ("original", "uploads", "locks"):
            (self.root / folder).mkdir()
        self.owner = get_user_model().objects.create_user(username="delete-owner")
        self.other = get_user_model().objects.create_user(username="delete-other")
        upload = Upload.objects.create(owner=self.owner, title="Delete", expected_size=4, state="COMPLETE")
        self.video = Video.objects.create(owner=self.owner, upload=upload, title="Delete", state="READY", published_generation=1)
        original_path(upload.id).write_bytes(b"data")
        self.job = Job.objects.create(video=self.video, generation=1, state="PROCESSING")
        self.attempt = Attempt.objects.create(job=self.job, number=1, state="RUNNING")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.owner)
        self.headers = {"X-CSRFToken": self.client.get("/api/auth/session").json()["csrf_token"]}
        self.url = f"/api/videos/{self.video.id}/delete"

    def post(self):
        return self.client.post(self.url, headers=self.headers)

    def test_owner_csrf_immediate_revocation_and_idempotent_delete(self):
        link = f"/media/{self.video.id}/master.m3u8"
        self.assertEqual(self.client.get(link).status_code, 200)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        other = Client(); other.force_login(self.other)
        self.assertEqual(other.post(self.url).status_code, 404)
        self.assertEqual(self.post().status_code, 200)
        self.video.refresh_from_db(); generation = self.video.generation
        for path in (link, f"/watch/{self.video.id}", f"/api/catalog/{self.video.id}",
                     f"/media/{self.video.id}/g1/v0/seg-000000.ts"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.get("/api/catalog").json()["videos"], [])
        self.assertFalse(original_path(self.video.upload_id).exists())
        self.assertEqual(self.post().status_code, 200)
        self.video.refresh_from_db(); self.assertEqual(self.video.generation, generation)
        self.assertEqual(len(journal.read_entries()[0]), 1)
        self.assertEqual(self.client.post(f"/api/videos/{self.video.id}/rename", data='{"title":"Again"}',
                         content_type="application/json", headers=self.headers).status_code, 409)

    def test_live_writer_lock_prevents_cleanup_but_not_revocation(self):
        with media_lock(self.video.id) as acquired:
            self.assertTrue(acquired)
            response = self.post()
            self.assertEqual((response.status_code, response.json()["state"]), (202, "DELETING"))
            self.assertTrue(original_path(self.video.upload_id).exists())
            self.assertEqual(self.client.get(f"/media/{self.video.id}/master.m3u8").status_code, 404)
        inode = (self.root / "locks" / str(self.video.id)).stat().st_ino
        self.assertEqual(clean_pending(), 1)
        self.assertEqual((self.root / "locks" / str(self.video.id)).stat().st_ino, inode)
        self.assertFalse(original_path(self.video.upload_id).exists())

    def test_interrupted_cleanup_resumes_and_old_worker_cannot_publish(self):
        with patch("vidharbor.deletion.remove", side_effect=OSError("injected interrupted cleanup")):
            self.assertEqual(self.post().status_code, 202)
        self.assertEqual(clean_pending(), 1)
        self.assertIsNone(begin_attempt(self.job.id, 1, 2, {}))
        finish_attempt(self.attempt.id, "success", {}, publish=publish_video)
        self.video.refresh_from_db(); self.job.refresh_from_db()
        self.assertEqual((self.video.state, self.job.state), ("DELETED", "CANCELLED"))

    def test_old_database_cannot_resurrect_journaled_deletion(self):
        self.assertEqual(self.post().status_code, 200)
        Video.objects.filter(pk=self.video.pk).update(state="READY", generation=1, published_generation=1, deleted_at=None)
        self.assertEqual(self.client.get(f"/watch/{self.video.id}").status_code, 404)
        apply_journal()
        self.video.refresh_from_db(); self.assertEqual(self.video.state, "DELETED")

    def test_corrupt_journal_fails_closed_and_restore_mode_blocks_public_access(self):
        self.assertEqual(self.post().status_code, 200)
        (self.root / "authority" / "head.json").write_text('{}')
        self.assertEqual(self.client.get("/api/catalog").status_code, 503)
        with self.settings(RESTORE_MODE=True):
            self.assertEqual(self.client.get("/health/live").status_code, 200)
            self.assertEqual(self.client.get("/health/ready").status_code, 503)
            self.assertEqual(self.client.get(f"/media/{self.video.id}/master.m3u8").status_code, 503)

    def test_parallel_deletion_requests_keep_one_durable_intent(self):
        def delete():
            try:
                revoke(self.video.id)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: delete(), range(2)))
        self.assertEqual(len(journal.read_entries()[0]), 1)
        self.video.refresh_from_db(); self.assertEqual(self.video.generation, 2)
