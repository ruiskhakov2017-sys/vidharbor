import tempfile
import threading
import queue
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection, connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from . import journal
from .deletion import revoke, clean, clean_pending, apply_journal
from .job_attempts import begin_attempt, finish_attempt
from .models import Attempt, Delivery, Job, Outbox, ReadyEvent, Upload, Video
from .publication import publish_video, ready_path, staging_path
from .storage import media_lock, original_path, sync_directory


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

    def test_cleanup_command_recovers_journaled_delete_after_transaction_rollback(self):
        from .notifications import attach_existing_ready, claim, finish
        self.job.state = "SUCCEEDED"; self.job.save(update_fields=["state"])
        self.attempt.state = "SUCCEEDED"; self.attempt.save(update_fields=["state"])
        event_id = uuid.uuid4()
        Outbox.objects.create(message_id=event_id, job=self.job, kind="video.ready", attempt_no=0,
            payload={"type": "video.ready", "version": 1, "event_id": str(event_id),
                     "video_id": str(self.video.id), "title": self.video.title,
                     "ready_at": timezone.now().isoformat(),
                     "playlist_url": f"https://video.example.invalid/media/{self.video.id}/master.m3u8"})
        self.assertEqual(attach_existing_ready(), 1)
        sending = claim(event_id)
        self.assertIsNotNone(sending)

        with self.assertRaisesRegex(RuntimeError, "rollback after durable intent"):
            with transaction.atomic():
                revoke(self.video.id)
                self.assertTrue(journal.is_deleted(self.video.id))
                raise RuntimeError("rollback after durable intent")

        self.video.refresh_from_db()
        self.assertEqual((self.video.state, self.video.generation, self.video.published_generation), ("READY", 1, 1))
        self.assertEqual(Delivery.objects.get(pk=event_id).state, "SENDING")
        self.assertTrue(original_path(self.video.upload_id).exists())
        intent = journal.deletion_record(self.video.id)
        self.assertIsNotNone(intent)
        self.assertEqual(self.client.get(f"/watch/{self.video.id}").status_code, 404)

        # An ordinary restarted cleaner must recover without a restore command.
        call_command("cleanup_uploads", stdout=StringIO())
        self.video.refresh_from_db()
        self.assertEqual(self.video.state, "DELETED")
        self.assertFalse(original_path(self.video.upload_id).exists())
        self.assertEqual(Delivery.objects.get(pk=event_id).state, "CANCELLED")
        finish(sending, "written")
        self.assertIsNone(claim(event_id))
        self.assertEqual(attach_existing_ready(), 0)
        self.assertEqual(Delivery.objects.get(pk=event_id).state, "CANCELLED")

        settled = (self.video.generation, self.video.deleted_at)
        call_command("cleanup_uploads", stdout=StringIO())
        self.video.refresh_from_db()
        self.assertEqual((self.video.generation, self.video.deleted_at), settled)
        self.assertEqual(journal.deletion_record(self.video.id), intent)
        self.assertEqual((Job.objects.count(), Outbox.objects.count(), ReadyEvent.objects.count()), (1, 1, 1))

    def test_replayed_intent_waits_for_writer_lock_and_cannot_republish(self):
        Video.objects.filter(pk=self.video.pk).update(state="QUEUED", published_generation=None)
        staged = staging_path(self.video.id)
        staged.mkdir(parents=True)
        (staged / "unfinished-output").write_bytes(b"partial encoding")
        with self.assertRaisesRegex(RuntimeError, "rollback with active writer"):
            with transaction.atomic():
                revoke(self.video.id)
                raise RuntimeError("rollback with active writer")
        self.video.refresh_from_db(); self.attempt.refresh_from_db()
        self.assertEqual((self.video.state, self.attempt.state), ("QUEUED", "RUNNING"))
        intent = journal.deletion_record(self.video.id)
        with media_lock(self.video.id) as acquired:
            self.assertTrue(acquired)
            inode = (self.root / "locks" / str(self.video.id)).stat().st_ino
            call_command("cleanup_uploads", stdout=StringIO())
            self.video.refresh_from_db(); self.job.refresh_from_db(); self.attempt.refresh_from_db()
            self.assertEqual((self.video.state, self.job.state, self.attempt.state), ("DELETING", "CANCELLED", "FAILED"))
            self.assertTrue(original_path(self.video.upload_id).exists())
            self.assertTrue(staged.exists())
            finish_attempt(self.attempt.id, "success", {}, publish=publish_video)
            self.assertIsNone(begin_attempt(self.job.id, 1, 2, {}))
            self.assertFalse(Outbox.objects.filter(kind="video.ready").exists())
        call_command("cleanup_uploads", stdout=StringIO())
        self.video.refresh_from_db()
        self.assertEqual((self.video.state, self.video.generation), ("DELETED", 2))
        self.assertFalse(original_path(self.video.upload_id).exists())
        self.assertFalse(staged.exists())
        self.assertEqual((self.root / "locks" / str(self.video.id)).stat().st_ino, inode)
        self.assertEqual(clean_pending(), 0)
        self.assertEqual(journal.deletion_record(self.video.id), intent)
        self.assertEqual((Job.objects.count(), Attempt.objects.count(), Outbox.objects.count()), (1, 1, 0))

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

    def publication_boundary(self, crash):
        """Real rename + PostgreSQL lock wait, with either commit or rollback."""
        Video.objects.filter(pk=self.video.pk).update(state="QUEUED", published_generation=None)
        staging = staging_path(self.video.id)
        staging.mkdir(parents=True)
        (staging / "publication-boundary-fixture").write_bytes(b"encoded test output")
        renamed, release = threading.Event(), threading.Event()
        backend_pid = queue.Queue()

        def pause_after_rename(folder):
            if not renamed.is_set():
                renamed.set()
                if not release.wait(10):
                    raise RuntimeError("publication barrier timed out")
                if crash:
                    raise RuntimeError("injected crash after rename")
            sync_directory(folder)

        def publish():
            try:
                with media_lock(self.video.id) as acquired:
                    assert acquired
                    finish_attempt(self.attempt.pk, "success", {}, publish=publish_video)
            finally:
                connections.close_all()

        def delete():
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    backend_pid.put(cursor.fetchone()[0])
                return revoke(self.video.id)
            finally:
                connections.close_all()

        with patch("vidharbor.publication.sync_directory", side_effect=pause_after_rename), ThreadPoolExecutor(max_workers=2) as pool:
            publishing = pool.submit(publish)
            try:
                self.assertTrue(renamed.wait(5))
                self.assertTrue(ready_path(self.video.id, 1).is_dir())
                self.assertEqual(self.client.get(f"/media/{self.video.id}/master.m3u8").status_code, 404)
                deleting = pool.submit(delete)
                pid = backend_pid.get(timeout=5)
                deadline = time.monotonic() + 5
                while True:
                    with connection.cursor() as cursor:
                        cursor.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", [pid])
                        waiting = cursor.fetchone()
                    if waiting == ("Lock",):
                        break
                    self.assertLess(time.monotonic(), deadline, "delete did not wait on publication's row lock")
                    time.sleep(.02)
            finally:
                release.set()
            if crash:
                with self.assertRaisesRegex(RuntimeError, "injected crash after rename"):
                    publishing.result(timeout=5)
            else:
                publishing.result(timeout=5)
            deleting.result(timeout=5)
        self.video.refresh_from_db()
        self.assertEqual((self.video.state, self.video.generation, self.video.published_generation), ("DELETING", 2, None))
        self.assertEqual(Outbox.objects.filter(job=self.job, kind="video.ready").count(), 0 if crash else 1)
        self.assertEqual(self.client.get(f"/watch/{self.video.id}").status_code, 404)
        self.assertTrue(clean(self.video.id))
        self.assertFalse(ready_path(self.video.id, 1).parent.exists())
        self.assertFalse(original_path(self.video.upload_id).exists())
        from .notifications import attach_existing_ready
        self.assertEqual(attach_existing_ready(), 0)

    def test_delete_between_rename_and_commit_serializes_with_publication(self):
        self.publication_boundary(crash=False)

    def test_delete_after_rename_before_rollback_cleans_uncommitted_files(self):
        self.publication_boundary(crash=True)
