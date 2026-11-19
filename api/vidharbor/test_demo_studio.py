from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from .models import Upload, Video


@override_settings(DEMO_STUDIO=True)
class DemoStudioTests(TestCase):
    def start_guest(self, client):
        csrf = client.get("/api/auth/session").json()["csrf_token"]
        response = client.post("/api/auth/guest", headers={"X-CSRFToken": csrf})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_guest_requires_post_and_csrf_and_is_disabled_outside_demo(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get("/api/auth/guest").status_code, 405)
        self.assertEqual(client.post("/api/auth/guest").status_code, 403)
        with self.settings(DEMO_STUDIO=False):
            csrf = client.get("/api/auth/session").json()["csrf_token"]
            self.assertEqual(client.post("/api/auth/guest", headers={"X-CSRFToken": csrf}).status_code, 404)
        self.assertFalse(get_user_model().objects.exists())

    def test_guest_keeps_ownership_across_requests_and_cannot_manage_other_guests(self):
        first, second = Client(enforce_csrf_checks=True), Client(enforce_csrf_checks=True)
        guest = self.start_guest(first)
        self.assertTrue(guest["guest"])
        self.assertEqual(self.start_guest(first)["username"], guest["username"])
        author = get_user_model().objects.get(username=guest["username"])
        self.assertFalse(author.has_usable_password())
        upload = Upload.objects.create(owner=author, title="Only mine", expected_size=1)
        video = Video.objects.create(owner=author, upload=upload, title=upload.title)
        other = self.start_guest(second)
        self.assertNotEqual(guest["username"], other["username"])
        self.assertEqual(second.get("/api/videos").json()["videos"], [])
        self.assertEqual(second.post(f"/api/videos/{video.pk}/delete", headers={
            "X-CSRFToken": other["csrf_token"],
        }).status_code, 404)
        self.assertEqual(first.get("/api/videos").json()["videos"][0]["id"], str(video.pk))
        with self.settings(DEMO_STUDIO=False):
            self.assertEqual(first.get("/api/videos").status_code, 401)

    def test_subpath_links_are_public_but_nginx_file_redirect_is_internal(self):
        author = get_user_model().objects.create_user(username="creator")
        upload = Upload.objects.create(owner=author, title="Video", expected_size=1)
        video = Video.objects.create(owner=author, upload=upload, title=upload.title,
                                     state=Video.State.READY, published_generation=1)
        self.client.force_login(author)
        with TemporaryDirectory() as journal, self.settings(PUBLIC_PATH="/vidharbor", JOURNAL_ROOT=Path(journal)):
            detail = self.client.get(f"/api/catalog/{video.pk}").json()
            cabinet = self.client.get("/api/videos").json()["videos"][0]
            self.assertEqual(detail["watch_url"], f"/vidharbor/watch/{video.pk}")
            self.assertEqual(cabinet["watch_url"], detail["watch_url"])
            self.assertEqual(detail["playlist_url"], f"/vidharbor/media/{video.pk}/master.m3u8")
            response = self.client.get(f"/media/{video.pk}/master.m3u8")
            self.assertEqual(response["X-Accel-Redirect"], f"/_vidharbor_hls/{video.pk}/g1/master.m3u8")

    def test_existing_author_keeps_their_session(self):
        author = get_user_model().objects.create_user(username="existing")
        self.client.force_login(author)
        session = self.start_guest(self.client)
        self.assertEqual(session["username"], author.username)
        self.assertFalse(session["guest"])
        self.assertEqual(get_user_model().objects.count(), 1)
