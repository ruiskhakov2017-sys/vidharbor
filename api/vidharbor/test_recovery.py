import tempfile
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import CommandError
from django.test import TransactionTestCase
from django.utils import timezone

from . import journal
from .management.commands.recovery import reconcile
from .models import Attempt, Job, Outbox, SheetAllocator, Upload, Video


class RecoveryTests(TransactionTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(self.settings(JOURNAL_ROOT=self.root))
        owner = get_user_model().objects.create_user(username="restore-owner")
        upload = Upload.objects.create(owner=owner, title="Restore", expected_size=1)
        self.video = Video.objects.create(owner=owner, upload=upload, title="Restore")
        self.job = Job.objects.create(video=self.video, generation=1)
        self.message = Outbox.objects.create(job=self.job, kind="video.transcode.v1", attempt_no=1,
            sent_at=timezone.now(), payload={"message_id": str(uuid.uuid4())})

    def test_requires_closed_restore_and_preserves_obligation_and_attempt_history(self):
        with self.assertRaises(CommandError): reconcile(apply=True)
        self.job.state = "PROCESSING"; self.job.save()
        attempt = Attempt.objects.create(job=self.job, number=1, state="RUNNING")
        before = dict(Attempt.objects.values().get(pk=attempt.pk))
        journal.append("sheet", uuid.uuid4(), {"row": 17, "snapshot_sha256": "0"*64})
        with self.settings(RESTORE_MODE=True):
            first = reconcile(apply=True); second = reconcile(apply=True)
        self.assertEqual(first["actions"], second["actions"])
        self.message.refresh_from_db(); self.assertIsNone(self.message.sent_at)
        self.assertEqual(Attempt.objects.values().get(pk=attempt.pk), before)
        self.assertEqual(SheetAllocator.objects.get(pk=1).next_row, 18)
        self.assertEqual((Job.objects.count(), Outbox.objects.count()), (1, 1))

    def test_active_job_without_command_is_explicit_failure(self):
        self.message.delete()
        with self.settings(RESTORE_MODE=True), self.assertRaisesRegex(CommandError, "no durable command"):
            reconcile(apply=True)

    def test_unapplied_deletion_authority_prevents_reconciliation(self):
        journal.append("delete", self.video.pk, {"video_id": str(self.video.pk), "upload_id": str(self.video.upload_id)})
        with self.settings(RESTORE_MODE=True), self.assertRaisesRegex(CommandError, "Apply deletion authority"):
            reconcile(apply=True)
