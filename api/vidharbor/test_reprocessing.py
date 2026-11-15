import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connections, transaction
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .models import Attempt, Job, Outbox, Upload, Video
from .public_videos import watch_url
from .storage import file_digest, media_lock, original_path


class ReprocessingTests(TransactionTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.samples = Path(cls.enterClassContext(tempfile.TemporaryDirectory()))
        cls.sample = cls.samples / "sample.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=64x64:rate=10",
            "-t", "1", "-c:v", "mpeg4", str(cls.sample),
        ], check=True, timeout=20)
        cls.unsupported = cls.samples / "unsupported.avi"
        subprocess.run([
            "ffmpeg", "-v", "error", "-i", str(cls.sample), "-c", "copy", str(cls.unsupported),
        ], check=True, timeout=20)

    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for name in ("locks", "original", "uploads"):
            (root / name).mkdir()
        self.enterContext(self.settings(MEDIA_ROOT=root))
        self.owner = get_user_model().objects.create_user(username="retry-owner")
        self.other = get_user_model().objects.create_user(username="retry-other")
        self.upload = Upload.objects.create(
            owner=self.owner, title="Источник", state=Upload.State.COMPLETE,
            expected_size=self.sample.stat().st_size, size=self.sample.stat().st_size,
            sha256=file_digest(self.sample),
        )
        shutil.copyfile(self.sample, original_path(self.upload.id))
        self.video = Video.objects.create(owner=self.owner, upload=self.upload, title="Видео")
        self.source = Job.objects.create(video=self.video, generation=1, state=Job.State.FAILED)
        self.mark_job_failed(self.source)
        self.url = self.route(self.source)
        self.client, self.headers = self.author_client(self.owner)

    def mark_job_failed(self, job, reason="disk_full"):
        job.state = Job.State.FAILED
        job.save(update_fields=["state"])
        return Attempt.objects.create(
            job=job, number=1, state=Attempt.State.FAILED, reason=reason, finished_at=timezone.now(),
        )

    def route(self, job):
        return f"/api/videos/{self.video.id}/jobs/{job.id}/retry"

    def author_client(self, author):
        client = Client(enforce_csrf_checks=True)
        client.force_login(author)
        csrf = client.get("/api/auth/session").json()["csrf_token"]
        return client, {"X-CSRFToken": csrf}

    def post(self, url=None):
        return self.client.post(url or self.url, headers=self.headers)

    def test_atomic_success_preserves_original_identity_and_history(self):
        original = original_path(self.upload.id)
        before = (original.read_bytes(), original.stat().st_ino, original.stat().st_mtime_ns)
        upload = Upload.objects.values().get(pk=self.upload.pk)
        source = Job.objects.values().get(pk=self.source.pk)
        attempt = list(Attempt.objects.values())
        link = watch_url(self.video.id)
        response = self.post()
        self.assertEqual(response.status_code, 201)
        child = Job.objects.get(retry_of=self.source)
        self.video.refresh_from_db()
        self.assertEqual((self.video.generation, child.generation, child.state), (2, 2, Job.State.QUEUED))
        self.assertEqual(response.json()["job"]["id"], str(child.id))
        self.assertEqual(watch_url(self.video.id), link)
        self.assertEqual((original.read_bytes(), original.stat().st_ino, original.stat().st_mtime_ns), before)
        self.assertEqual(Upload.objects.values().get(pk=self.upload.pk), upload)
        self.assertEqual(Job.objects.values().get(pk=self.source.pk), source)
        self.assertEqual(list(Attempt.objects.values()), attempt)
        entry = Outbox.objects.get(job=child)
        self.assertIsNone(entry.sent_at)
        self.assertEqual((entry.kind, entry.attempt_no), ("video.transcode.v1", 1))
        self.assertEqual(entry.payload["message_id"], str(entry.message_id))
        self.assertEqual(entry.payload["generation"], 2)
        self.assertEqual(entry.payload["job_id"], str(child.id))
        self.assertEqual(entry.payload["video_id"], str(self.video.id))
        self.assertEqual(entry.payload["next_attempt_no"], 1)

    def test_ffprobe_timeout_is_retryable_and_does_not_consume_receipt(self):
        original = original_path(self.upload.id)
        before = (file_digest(original), original.stat().st_ino)
        with patch("vidharbor.media_probe.subprocess.run", side_effect=subprocess.TimeoutExpired("ffprobe", 20)):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.video.refresh_from_db()
        self.assertEqual((self.video.generation, Job.objects.count(), Outbox.objects.count()), (1, 1, 0))
        self.assertEqual((file_digest(original), original.stat().st_ino), before)
        self.assertEqual(self.post().status_code, 201)

    def test_worker_probe_timeout_uses_bounded_temporary_failure_path(self):
        from .transcode import encode
        from .media_probe import ProbeTimeout
        with patch("vidharbor.transcode.probe_video", side_effect=ProbeTimeout("budget")):
            self.assertEqual(encode(self.sample, self.samples, 0, threading.Event(), timeout=10),
                             ("retry", "probe_timeout"))

    def test_old_request_returns_same_failed_successor_even_after_next_retry(self):
        first = self.post().json()["job"]
        child = Job.objects.get(pk=first["id"])
        self.mark_job_failed(child, "worker_lost")
        replay = self.post()
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()["job"]["id"], first["id"])
        self.assertEqual(replay.json()["job"]["state"], "FAILED")
        self.assertEqual(Job.objects.count(), 2)
        self.assertEqual(Outbox.objects.count(), 1)
        third = self.post(self.route(child))
        self.assertEqual(third.status_code, 201)
        self.assertEqual(third.json()["job"]["generation"], 3)
        self.assertEqual(self.post().json()["job"]["id"], first["id"])
        self.assertEqual(Job.objects.count(), 3)
        self.assertEqual(Outbox.objects.count(), 2)

    def test_receipt_replay_when_successor_active_or_ready(self):
        # State-only receipt test; real FFmpeg/READY/HLS is covered by smoke_t5012.py.
        child = Job.objects.get(pk=self.post().json()["job"]["id"])
        for job_state, video_state in (("PROCESSING", "QUEUED"), ("SUCCEEDED", "READY")):
            Job.objects.filter(pk=child.pk).update(state=job_state)
            Video.objects.filter(pk=self.video.pk).update(state=video_state)
            result = self.post()
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()["job"]["id"], str(child.id))
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (2, 1))

    def test_parallel_requests_create_one_generation_and_outbox(self):
        barrier = threading.Barrier(4)

        def request():
            try:
                client, headers = self.author_client(self.owner)
                barrier.wait(timeout=15)
                response = client.post(self.url, headers=headers)
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: request(), range(4)))
        self.assertEqual(sorted(status for status, _ in results), [200, 200, 200, 201])
        self.assertEqual(len({body["job"]["id"] for _, body in results}), 1)
        self.video.refresh_from_db()
        self.assertEqual(self.video.generation, 2)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (2, 1))

    def test_database_constraint_allows_only_one_successor(self):
        child = Job.objects.get(pk=self.post().json()["job"]["id"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Job.objects.create(video=self.video, generation=3, retry_of=self.source)
        self.assertEqual(Job.objects.get(retry_of=self.source).id, child.id)

    def test_outbox_failure_rolls_back_generation_job_and_relation(self):
        with patch("vidharbor.reprocessing.Outbox.objects.create", side_effect=RuntimeError("test rollback")):
            with self.assertRaisesRegex(RuntimeError, "test rollback"):
                self.post()
        self.video.refresh_from_db()
        self.assertEqual(self.video.generation, 1)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (1, 0))
        self.assertEqual(self.post().status_code, 201)

    def test_owner_session_csrf_and_wrong_video(self):
        client, headers = self.author_client(self.other)
        self.assertEqual(client.post(self.url, headers=headers).status_code, 404)
        self.assertEqual(self.client.post(self.url).status_code, 403)
        anonymous = Client(enforce_csrf_checks=True)
        token = anonymous.get("/api/auth/session").json()["csrf_token"]
        self.assertEqual(anonymous.post(self.url, headers={"X-CSRFToken": token}).status_code, 401)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(self.post(self.url.replace(str(self.video.id), str(self.upload.id))).status_code, 404)
        self.assertEqual(Job.objects.count(), 1)

    def test_reject_active_successful_and_deleting_states(self):
        for state in ("QUEUED", "PROCESSING", "RETRY_WAIT", "SUCCEEDED"):
            Job.objects.filter(pk=self.source.pk).update(state=state)
            self.assertEqual(self.post().status_code, 409)
        Job.objects.filter(pk=self.source.pk).update(state="FAILED")
        for state in ("READY", "DELETING", "DELETED"):
            Video.objects.filter(pk=self.video.pk).update(state=state)
            self.assertEqual(self.post().status_code, 409)
        self.assertEqual(Job.objects.count(), 1)

    def test_reject_nonrepairable_and_unknown_failure(self):
        for reason in ("invalid_video", "ffmpeg_decode_or_encode", "invalid_hls", "unknown"):
            Attempt.objects.filter(job=self.source).update(reason=reason)
            self.assertEqual(self.post().status_code, 409)
            listing = self.client.get("/api/videos").json()["videos"][0]
            self.assertFalse(listing["can_retry"])
        self.assertEqual(Job.objects.count(), 1)

    def test_missing_original(self):
        original_path(self.upload.id).unlink()
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertIn("не найден", response.json()["error"])
        self.assertEqual(Job.objects.count(), 1)

    def test_changed_or_unreadable_container_is_not_queued(self):
        path = original_path(self.upload.id)
        path.write_bytes(b"not a video")
        self.assertEqual(self.post().status_code, 422)
        Upload.objects.filter(pk=self.upload.pk).update(size=path.stat().st_size, sha256=file_digest(path))
        self.assertEqual(self.post().status_code, 422)  # Hash matches, real ffprobe rejects it.
        self.assertEqual(Job.objects.count(), 1)

    def test_symlink_original_rejected(self):
        path = original_path(self.upload.id)
        path.unlink()
        path.symlink_to(self.sample)
        self.assertEqual(self.post().status_code, 422)
        self.assertEqual(Job.objects.count(), 1)

    def test_unsupported_container_with_matching_hash_is_rejected(self):
        path = original_path(self.upload.id)
        path.write_bytes(self.unsupported.read_bytes())
        Upload.objects.filter(pk=self.upload.pk).update(size=path.stat().st_size, sha256=file_digest(path))
        response = self.post()
        self.assertEqual(response.status_code, 422)
        self.assertIn("не поддерживается", response.json()["error"])
        self.assertEqual(Job.objects.count(), 1)

    def test_inconsistent_failure_history_is_not_retryable(self):
        Attempt.objects.filter(job=self.source).update(state=Attempt.State.RUNNING)
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(self.client.get("/api/videos").json()["videos"][0]["can_retry"])

    def test_validation_unavailable_is_retryable_without_new_job(self):
        with patch("vidharbor.reprocessing.check_video", side_effect=FileNotFoundError(2, "missing", "ffprobe")):
            response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response["Retry-After"], "1")
        self.assertEqual(Job.objects.count(), 1)

    def test_stale_source_without_successor_is_rejected(self):
        Video.objects.filter(pk=self.video.pk).update(generation=2)
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(Job.objects.count(), 1)

    def test_busy_s04_lock_does_not_create_work(self):
        before = original_path(self.upload.id).read_bytes()
        with media_lock(self.video.id) as held:
            self.assertTrue(held)
            response = self.post()
        self.assertEqual(response.status_code, 423)
        self.assertEqual(response["Retry-After"], "1")
        self.video.refresh_from_db()
        self.assertEqual(self.video.generation, 1)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (1, 0))
        self.assertEqual(original_path(self.upload.id).read_bytes(), before)
        self.assertEqual(self.post().status_code, 201)

    def test_existing_receipt_does_not_revalidate_missing_source(self):
        first = self.post().json()["job"]
        original_path(self.upload.id).unlink()
        result = self.post()
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["job"]["id"], first["id"])
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (2, 1))
