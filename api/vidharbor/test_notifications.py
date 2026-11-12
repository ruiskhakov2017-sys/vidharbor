import json
import copy
import threading
import uuid
from tempfile import NamedTemporaryFile
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import close_old_connections, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from .models import Delivery, Job, Outbox, ReadyEvent, ReceiverRow, Upload, Video
from .notifications import (
    OFFSETS, Webhook, cancel_locked, claim, enqueue_existing, finish,
    retry_manually, run_once, signature,
)
from .test_receiver import receiver, write_row


class NotificationTests(TransactionTestCase):
    def setUp(self):
        self.now = timezone.now()
        self.secret = b"test-only-" + b"x" * 32
        owner = get_user_model().objects.create_user(username="notifier-author")
        upload = Upload.objects.create(owner=owner, title="Original", expected_size=1)
        # S04 is not implemented: sample READY state/event, no publication claim.
        self.video = Video.objects.create(upload=upload, owner=owner, title="Original", state="READY")
        event_id = uuid.uuid4()
        self.payload = {
            "type": "video.ready", "version": 1, "event_id": str(event_id),
            "video_id": str(self.video.pk), "title": "Original",
            "ready_at": self.now.isoformat(), "sheet_row": 2,
            "playlist_url": "https://video.example.invalid/media/video/master.m3u8",
        }
        self.event = ReadyEvent.objects.create(event_id=event_id, video=self.video, generation=1,
                                               sheet_row=2, payload=self.payload)
        self.delivery = enqueue_existing(event_id)
        Delivery.objects.filter(pk=event_id).update(due_at=self.now)

    def refresh(self):
        self.delivery.refresh_from_db()
        return self.delivery

    def threaded(self, function):
        def wrapped(*args):
            close_old_connections()
            try:
                return function(*args)
            finally:
                close_old_connections()
        return wrapped

    def test_five_attempts_and_manual_retry_preserve_snapshot(self):
        for index, offset in enumerate(OFFSETS, 1):
            now = self.now + timedelta(seconds=offset)
            sending = claim(self.event.pk, now)
            self.assertEqual(sending.attempts, index)
            finish(sending, "retry", "transport_error", now=now)
        self.assertEqual(self.refresh().state, "FAILED")
        retry_manually(self.event.pk, self.now + timedelta(hours=2))
        sending = claim(self.event.pk, self.now + timedelta(hours=2))
        self.assertEqual(sending.total_attempts, 6)
        self.event.refresh_from_db()
        self.assertEqual(self.event.payload, self.payload)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (0, 0))

    def test_lease_recovery_and_stale_completion(self):
        first = claim(self.event.pk, self.now)
        self.assertIsNone(claim(self.event.pk, self.now + timedelta(seconds=59)))
        second = claim(self.event.pk, self.now + timedelta(seconds=61))
        self.assertNotEqual(first.token, second.token)
        self.assertEqual(first.event_id, second.event_id)
        finish(first, "written", now=self.now + timedelta(seconds=62))
        self.assertEqual(self.refresh().state, "SENDING")
        finish(second, "written", now=self.now + timedelta(seconds=63))
        self.assertEqual(self.refresh().state, "WRITTEN")
        self.assertIsNone(claim(self.event.pk, self.now + timedelta(seconds=64)))

    def test_expired_budget_and_retry_after(self):
        sending = claim(self.event.pk, self.now)
        finish(sending, "retry", "http_429", 120, now=self.now)
        self.assertEqual(self.refresh().due_at, self.now + timedelta(seconds=120))
        sending = claim(self.event.pk, self.now + timedelta(seconds=120))
        finish(sending, "retry", "http_429", 3600, now=self.now + timedelta(seconds=120))
        self.assertEqual(self.refresh().state, "FAILED")
        retry_manually(self.event.pk, self.now)
        claim(self.event.pk, self.now)
        self.assertIsNone(claim(self.event.pk, self.now + timedelta(hours=1)))
        self.assertEqual(self.refresh().state, "FAILED")

    def test_not_ready_generation_and_deleted_never_send(self):
        for state, generation in (("QUEUED", 1), ("READY", 2), ("DELETING", 1)):
            Video.objects.filter(pk=self.video.pk).update(state=state, generation=generation)
            Delivery.objects.filter(pk=self.event.pk).update(state="PENDING")
            self.assertIsNone(claim(self.event.pk, self.now))
            self.assertEqual(self.refresh().state, "CANCELLED")

    def test_delete_after_claim_cancels_stale_result(self):
        sending = claim(self.event.pk, self.now)
        with transaction.atomic():
            video = Video.objects.select_for_update().get(pk=self.video.pk)
            video.state = "DELETING"
            video.generation += 1
            video.save()
            cancel_locked(video)
        finish(sending, "written", now=self.now)
        self.assertEqual(self.refresh().state, "CANCELLED")
        with self.assertRaises(ValueError):
            retry_manually(self.event.pk, self.now)

    def test_delete_holds_same_lock_as_claim(self):
        # Real PostgreSQL row lock: deletion wins before sender is allowed to claim.
        started = threading.Event()
        def start_claim():
            started.set()
            return claim(self.event.pk, self.now)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                video = Video.objects.select_for_update().get(pk=self.video.pk)
                future = pool.submit(self.threaded(start_claim))
                self.assertTrue(started.wait(5))
                video.state = "DELETING"
                video.save()
                cancel_locked(video)
            self.assertIsNone(future.result(timeout=10))
        self.assertEqual(self.refresh().state, "CANCELLED")

    def test_parallel_claims_only_one_sender(self):
        barrier = threading.Barrier(2)
        def simultaneous():
            barrier.wait(timeout=5)
            return claim(self.event.pk, self.now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(self.threaded(lambda _: simultaneous()), range(2)))
        self.assertEqual(sum(item is not None for item in results), 1)
        self.assertEqual(self.refresh().total_attempts, 1)

    def test_different_ids_same_empty_row_are_serialized(self):
        other = dict(self.payload, event_id=str(uuid.uuid4()))
        barrier = threading.Barrier(2)
        def simultaneous(payload):
            barrier.wait(timeout=5)
            return write_row(payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(self.threaded(simultaneous), [self.payload, other]))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(ReceiverRow.objects.count(), 1)
        row = ReceiverRow.objects.get(number=2)
        self.assertIn(str(row.event_id), [self.payload["event_id"], other["event_id"]])
        self.assertEqual(str(row.event_id), row.payload["event_id"])

    def test_parallel_duplicates_and_lost_reply(self):
        barrier = threading.Barrier(2)
        def simultaneous(_):
            barrier.wait(timeout=5)
            return write_row(self.payload)
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(self.threaded(simultaneous), range(2))), [200, 200])
        sending = claim(self.event.pk, self.now)
        finish(sending, "retry", "transport_error", now=self.now)
        sending = claim(self.event.pk, self.now + timedelta(seconds=60))
        self.assertEqual(write_row(sending.event.payload), 200)
        finish(sending, "written", now=self.now + timedelta(seconds=60))
        self.assertEqual(ReceiverRow.objects.count(), 1)
        self.assertEqual(self.refresh().state, "WRITTEN")
        self.assertFalse(Job.objects.exists())
        self.assertEqual(write_row(dict(self.payload, title="Changed")), 409)

    def test_http_response_lost_after_write_then_retry(self):
        webhook = self.start_receiver()
        original_open = webhook.opener.open
        def lose_response(*args, **kwargs):
            with original_open(*args, **kwargs) as response:
                self.assertEqual(response.status, 200)
                response.read()
            raise URLError("simulated lost response after write")
        sending = claim(self.event.pk, self.now)
        with patch.object(webhook.opener, "open", side_effect=lose_response):
            outcome, reason, delay = webhook.send(sending.event)
        self.assertEqual(outcome, "retry")
        self.assertEqual(ReceiverRow.objects.count(), 1)
        finish(sending, outcome, reason, delay, now=self.now)
        sending = claim(self.event.pk, self.now + timedelta(minutes=1))
        outcome, reason, delay = webhook.send(sending.event)
        finish(sending, outcome, reason, delay, now=self.now + timedelta(minutes=1))
        self.assertEqual(self.refresh().state, "WRITTEN")
        self.assertEqual(ReceiverRow.objects.get().payload, self.payload)
        self.assertFalse(Job.objects.exists())

    def test_management_command_attaches_existing_event_and_delivers(self):
        webhook = self.start_receiver()
        self.delivery.delete()
        with NamedTemporaryFile() as secret_file:
            secret_file.write(self.secret)
            secret_file.flush()
            call_command("notify_ready", url=webhook.url, allow_host=["127.0.0.1"],
                         secret_file=secret_file.name, allow_test_loopback=True, once=True)
        self.assertEqual(Delivery.objects.get(pk=self.event.pk).state, "WRITTEN")
        self.assertEqual(ReadyEvent.objects.count(), 1)

    def start_receiver(self):
        server = receiver(self.secret, ("127.0.0.1", 0))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def cleanup():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(cleanup)
        url = f"http://127.0.0.1:{server.server_port}/video-ready"
        return Webhook(url, ["127.0.0.1"], self.secret, allow_test_loopback=True)

    def test_real_http_ack_signature_and_snapshot(self):
        webhook = self.start_receiver()
        # Rename after publication does not change the snapshot.
        Video.objects.filter(pk=self.video.pk).update(title="Renamed")
        self.assertTrue(run_once(webhook, self.now))
        self.assertEqual(self.refresh().state, "WRITTEN")
        self.assertEqual(ReceiverRow.objects.get().payload, self.payload)
        self.assertFalse(run_once(webhook))
        wrong = Webhook(webhook.url, ["127.0.0.1"], b"bad-secret-" * 4, allow_test_loopback=True)
        self.assertEqual(wrong.send(self.event)[:2], ("blocked", "http_403"))
        self.assertEqual(webhook.send(self.event, self.now - timedelta(hours=1))[:2], ("blocked", "http_403"))

    def test_http_different_ids_row_conflict(self):
        webhook = self.start_receiver()
        other = copy.copy(self.event)
        other.event_id = uuid.uuid4()
        other.payload = dict(self.payload, event_id=str(other.event_id))
        barrier = threading.Barrier(2)
        def simultaneous(event):
            barrier.wait(timeout=5)
            return webhook.send(event)[0]
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(simultaneous, [self.event, other])), ["blocked", "written"])
        self.assertEqual(ReceiverRow.objects.count(), 1)

    def test_ack_and_redirect_retry_after_classification(self):
        webhook = self.start_receiver()
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return b'{"status":"written"}'
        with patch.object(webhook.opener, "open", return_value=Response()):
            self.assertEqual(webhook.send(self.event)[:2], ("retry", "invalid_ack"))
        for code, expected in ((302, "blocked"), (401, "blocked"), (409, "blocked"),
                               (429, "retry"), (503, "retry")):
            error = HTTPError(webhook.url, code, "test", {"Retry-After": "120"}, None)
            with patch.object(webhook.opener, "open", side_effect=error):
                outcome, _, delay = webhook.send(self.event)
            self.assertEqual(outcome, expected)
            if code in (429, 503):
                self.assertEqual(delay, 120)

    def test_destination_validation_and_fresh_signatures(self):
        for url in ("http://public.example/hook", "https://other.example/hook",
                    "https://user:pass@allowed.example/hook"):
            with self.assertRaises(ValueError):
                Webhook(url, ["allowed.example"], self.secret)
        self.assertNotEqual(signature(self.secret, "1", b"same"),
                            signature(self.secret, "2", b"same"))
        with self.assertRaises(ValueError):
            Webhook("https://allowed.example/hook", ["allowed.example"], b"short")

