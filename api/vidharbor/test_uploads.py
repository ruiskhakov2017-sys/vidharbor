import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import Client, TransactionTestCase
from django.utils import timezone

from .management.commands.cleanup_uploads import expire_uploads
from .models import Job, Outbox, Upload, Video
from .storage import file_digest, media_lock, original_path, part_path


class UploadTests(TransactionTestCase):
    def setUp(self):
        media = self.enterContext(tempfile.TemporaryDirectory())
        root = Path(media)
        for directory in ("uploads", "original", "locks"):
            (root / directory).mkdir()
        self.enterContext(self.settings(MEDIA_ROOT=root))
        self.author = get_user_model().objects.create_user(username="author")
        self.client = Client(enforce_csrf_checks=True)
        self.client.force_login(self.author)
        csrf = self.client.get("/api/auth/session").json()["csrf_token"]
        self.headers = {"X-CSRFToken": csrf}

    def create(self, size):
        response = self.client.post(
            "/api/uploads",
            {"title": "Тест", "size": size},
            content_type="application/json",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201)
        return Upload.objects.get(pk=response.json()["id"])

    def complete(self, upload):
        return self.client.post(f"/api/uploads/{upload.id}/complete", headers=self.headers)

    def test_rollback_after_rename_then_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "valid.mp4"
            subprocess.run(
                [
                    "ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i",
                    "testsrc2=size=64x64:rate=10", "-t", "1", "-c:v", "mpeg4", str(fixture),
                ],
                check=True,
                timeout=20,
            )
            content = fixture.read_bytes()
            digest = file_digest(fixture)
        upload = self.create(len(content))
        response = self.client.put(
            f"/api/uploads/{upload.id}/content",
            content,
            content_type="application/octet-stream",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)

        with patch("vidharbor.uploads.Outbox.objects.create", side_effect=RuntimeError("fail before commit")):
            with self.assertRaisesRegex(RuntimeError, "fail before commit"):
                self.complete(upload)
        upload.refresh_from_db()
        self.assertEqual(upload.state, Upload.State.VERIFYING)
        self.assertEqual(file_digest(original_path(upload.id)), digest)
        self.assertFalse(part_path(upload.id).exists())
        self.assertEqual((Video.objects.count(), Job.objects.count(), Outbox.objects.count()), (0, 0, 0))

        result = self.complete(upload)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(self.complete(upload).json(), result.json())
        self.assertEqual((Video.objects.count(), Job.objects.count(), Outbox.objects.count()), (1, 1, 1))
        upload.refresh_from_db()
        self.assertEqual(upload.state, Upload.State.COMPLETE)

    def test_cleanup_preserves_active_fresh_and_original(self):
        old, fresh, active, saved, abandoned, fresh_original, active_original, with_job = (
            self.create(3) for _ in range(8)
        )
        for upload in (old, fresh, active):
            part_path(upload.id).write_bytes(b"abc")
        saved.state = Upload.State.COMPLETE
        saved.save(update_fields=["state"])
        original_path(saved.id).write_bytes(b"abc")
        for upload in (abandoned, fresh_original, active_original, with_job):
            upload.state = Upload.State.VERIFYING
            upload.save(update_fields=["state"])
            original_path(upload.id).write_bytes(b"abc")
        video = Video.objects.create(upload=with_job, owner=self.author, title=with_job.title)
        Job.objects.create(video=video, generation=video.generation)
        Upload.objects.filter(id__in=[old.id, active.id, saved.id, abandoned.id, active_original.id, with_job.id]).update(
            updated_at=timezone.now() - timedelta(hours=25)
        )

        with media_lock(active.id) as acquired, media_lock(active_original.id) as original_acquired:
            self.assertTrue(acquired)
            self.assertTrue(original_acquired)
            self.assertEqual(expire_uploads(), 2)
            self.assertEqual(expire_uploads(), 0)
            self.assertTrue(part_path(active.id).exists())
            self.assertTrue(original_path(active_original.id).exists())
        old.refresh_from_db()
        abandoned.refresh_from_db()
        self.assertEqual(old.state, Upload.State.EXPIRED)
        self.assertEqual(abandoned.state, Upload.State.EXPIRED)
        self.assertFalse(part_path(old.id).exists())
        self.assertFalse(original_path(abandoned.id).exists())
        self.assertTrue(part_path(fresh.id).exists())
        self.assertTrue(original_path(fresh_original.id).exists())
        self.assertTrue(original_path(saved.id).exists())
        self.assertTrue(original_path(with_job.id).exists())

    def test_storage_reserve_and_hash_mismatch(self):
        with patch("vidharbor.uploads.has_space", return_value=False):
            response = self.client.post(
                "/api/uploads",
                {"title": "Тест", "size": 3},
                content_type="application/json",
                headers=self.headers,
            )
        self.assertEqual(response.status_code, 507)
        self.assertEqual(Upload.objects.count(), 0)
        upload = self.create(3)
        response = self.client.put(
            f"/api/uploads/{upload.id}/content",
            b"abc",
            content_type="application/octet-stream",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        part_path(upload.id).write_bytes(b"xyz")
        self.assertEqual(self.complete(upload).status_code, 422)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (0, 0))
