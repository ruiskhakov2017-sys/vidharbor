import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from .models import Job, Outbox, Upload, Video


class RenameVideoTests(TestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(username="owner")
        self.other = get_user_model().objects.create_user(username="other")
        self.upload = Upload.objects.create(
            owner=self.owner, title="Оригинал", expected_size=123, size=123,
            sha256="a" * 64, state=Upload.State.COMPLETE,
        )
        self.video = Video.objects.create(owner=self.owner, upload=self.upload, title="Прежнее")
        self.job = Job.objects.create(video=self.video, generation=self.video.generation)
        self.outbox = Outbox.objects.create(
            job=self.job, kind="video.transcode.v1", attempt_no=1, payload={"title": "Прежнее"},
        )
        self.url = f"/api/videos/{self.video.id}/rename"
        self.client = Client(enforce_csrf_checks=True)
        self.login(self.owner)

    def login(self, user):
        self.client.force_login(user)
        token = self.client.get("/api/auth/session").json()["csrf_token"]
        self.headers = {"X-CSRFToken": token}

    def rename(self, title, url=None):
        return self.client.post(
            url or self.url, {"title": title}, content_type="application/json", headers=self.headers,
        )

    def test_rename_preserves_identity_original_and_processing(self):
        before_video = Video.objects.values().get(pk=self.video.pk)
        before_upload = Upload.objects.values().get(pk=self.upload.pk)
        before_job = Job.objects.values().get(pk=self.job.pk)
        before_outbox = Outbox.objects.values().get(pk=self.outbox.pk)
        for _ in range(2):
            response = self.rename("  Новое название  ")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["id"], str(self.video.pk))
            self.assertEqual(response.json()["title"], "Новое название")
        self.assertEqual(
            Video.objects.values().get(pk=self.video.pk), {**before_video, "title": "Новое название"},
        )
        self.assertEqual(Upload.objects.values().get(pk=self.upload.pk), before_upload)
        self.assertEqual(Job.objects.values().get(pk=self.job.pk), before_job)
        self.assertEqual(Outbox.objects.values().get(pk=self.outbox.pk), before_outbox)
        self.assertEqual((Video.objects.count(), Job.objects.count(), Outbox.objects.count()), (1, 1, 1))
        self.assertEqual(self.client.get("/api/videos").json()["videos"][0]["title"], "Новое название")

    def test_other_author_cannot_rename_or_list_video(self):
        self.login(self.other)
        self.assertEqual(self.client.get("/api/videos").json(), {"videos": []})
        self.assertEqual(self.rename("Чужое").status_code, 404)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, "Прежнее")

    def test_requires_session_and_csrf(self):
        response = self.client.post(self.url, {"title": "Без CSRF"}, content_type="application/json")
        self.assertEqual(response.status_code, 403)
        self.client.logout()
        token = self.client.get("/api/auth/session").json()["csrf_token"]
        self.headers = {"X-CSRFToken": token}
        self.assertEqual(self.rename("Без входа").status_code, 401)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, "Прежнее")

    def test_validation_and_missing_video(self):
        for title in (None, 123, [], {}, "", "   ", "a" * 201, "bad\0title"):
            with self.subTest(title=title):
                self.assertEqual(self.rename(title).status_code, 400)
        self.video.refresh_from_db()
        self.assertEqual(self.video.title, "Прежнее")
        self.assertEqual(self.rename("я" * 200).status_code, 200)
        self.assertEqual(self.rename("Название", f"/api/videos/{uuid.uuid4()}/rename").status_code, 404)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_invalid_request_body(self):
        for body in ("{", "[]", "null"):
            self.assertEqual(self.client.post(
                self.url, body, content_type="application/json", headers=self.headers,
            ).status_code, 400)
        self.assertEqual(self.client.post(self.url, {"title": "form"}, headers=self.headers).status_code, 400)
