import copy
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase
from django.utils import timezone

from . import journal
from .deletion import revoke
from .models import Delivery, Job, Outbox, ReadyEvent, SheetAllocator, Upload, Video
from .notifications import attach_existing_ready, claim, finish


class ReadyIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(self.settings(JOURNAL_ROOT=self.root))
        self.owner = get_user_model().objects.create_user(username="ready-owner")

    def source(self):
        upload = Upload.objects.create(owner=self.owner, title="Original", expected_size=1)
        video = Video.objects.create(upload=upload, owner=self.owner, title="Original", state="READY", published_generation=1)
        job = Job.objects.create(video=video, generation=1, state="SUCCEEDED")
        event_id = uuid.uuid4()
        event = Outbox.objects.create(message_id=event_id, job=job, kind="video.ready", attempt_no=0,
            payload={"type": "video.ready", "version": 1, "event_id": str(event_id), "video_id": str(video.id),
                     "title": "Original", "ready_at": timezone.now().isoformat(),
                     "playlist_url": f"https://video.example.invalid/media/{video.id}/master.m3u8"})
        return video, event

    def test_existing_ready_snapshot_is_attached_without_regenerating_event(self):
        video, source = self.source(); before = copy.deepcopy(source.payload)
        Video.objects.filter(pk=video.pk).update(title="Renamed")
        self.assertEqual(attach_existing_ready(), 1)
        self.assertEqual(attach_existing_ready(), 0)
        event = ReadyEvent.objects.get(pk=source.pk)
        self.assertEqual(event.source_id, source.pk)
        self.assertEqual(event.payload, {**before, "sheet_row": 2})
        source.refresh_from_db(); self.assertEqual(source.payload, before)
        self.assertEqual((Job.objects.count(), Outbox.objects.count(), Delivery.objects.count()), (1, 1, 1))

    def test_db_rollback_never_reuses_reserved_sheet_row(self):
        _, source = self.source()
        with patch("vidharbor.notifications.ReadyEvent.objects.create", side_effect=RuntimeError("DB rollback")):
            with self.assertRaises(RuntimeError): attach_existing_ready()
        self.assertFalse(ReadyEvent.objects.exists())
        self.assertEqual(len(journal.read_entries()[0]), 1)
        attach_existing_ready(); self.assertEqual(ReadyEvent.objects.get(pk=source.pk).sheet_row, 2)
        _, other = self.source(); attach_existing_ready()
        self.assertEqual(ReadyEvent.objects.get(pk=other.pk).sheet_row, 3)
        self.assertEqual(SheetAllocator.objects.get(pk=1).next_row, 4)

    def test_parallel_attachment_uses_distinct_rows_and_same_source_ids(self):
        self.source(); self.source()
        barrier = threading.Barrier(2)
        def work():
            try:
                barrier.wait(timeout=10)
                return attach_existing_ready()
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: work(), range(2)))
        self.assertEqual(set(ReadyEvent.objects.values_list("sheet_row", flat=True)), {2, 3})
        self.assertEqual(Delivery.objects.count(), 2)

    def test_real_deletion_cancels_claim_and_ignores_late_ack(self):
        video, source = self.source(); attach_existing_ready()
        sending = claim(source.pk)
        self.assertIsNotNone(sending)
        revoke(video.id)
        finish(sending, "written")
        self.assertEqual(Delivery.objects.get(pk=source.pk).state, "CANCELLED")
        self.assertIsNone(claim(source.pk))

    def test_no_delivery_before_ready_and_restore_mode_never_sends(self):
        video, source = self.source()
        Video.objects.filter(pk=video.pk).update(state="QUEUED")
        self.assertEqual(attach_existing_ready(), 0)
        Video.objects.filter(pk=video.pk).update(state="READY")
        attach_existing_ready()
        with self.settings(RESTORE_MODE=True): self.assertIsNone(claim(source.pk))
