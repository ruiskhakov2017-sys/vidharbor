import uuid
from threading import Event

from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from .job_attempts import begin_attempt, finish_attempt
from .models import Attempt, Job, Outbox, Upload, Video


class JobAttemptTests(TransactionTestCase):
    def setUp(self):
        author = get_user_model().objects.create_user(username="attempt-author")
        upload = Upload.objects.create(owner=author, title="video", expected_size=1)
        self.video = Video.objects.create(upload=upload, owner=author, title="video")
        self.job = Job.objects.create(video=self.video, generation=1)
        self.payload = {
            "job_id": str(self.job.id),
            "video_id": str(self.video.id),
            "generation": 1,
            "next_attempt_no": 1,
            "message_id": str(uuid.uuid4()),
        }

    def test_retry_schedule_and_limit(self):
        payload = self.payload
        for number, delay in ((1, 30), (2, 120)):
            attempt = begin_attempt(self.job.id, 1, number, payload)
            before = timezone.now()
            finish_attempt(attempt, "retry", payload, "temporary")
            event = Outbox.objects.get(job=self.job, kind="video.transcode.v1", attempt_no=number + 1)
            self.assertGreaterEqual((event.due_at - before).total_seconds(), delay)
            payload = event.payload
        attempt = begin_attempt(self.job.id, 1, 3, payload)
        finish_attempt(attempt, "retry", payload, "temporary")
        self.job.refresh_from_db()
        self.assertEqual(self.job.state, Job.State.FAILED)
        self.assertEqual(self.job.attempts.count(), 3)
        self.assertEqual(Outbox.objects.filter(job=self.job, kind="video.failed.v1").count(), 1)

    def test_recovery_and_duplicate_after_success(self):
        attempt = begin_attempt(self.job.id, 1, 1, self.payload)
        self.assertIsNotNone(attempt)
        self.assertIsNone(begin_attempt(self.job.id, 1, 1, self.payload))
        previous = Attempt.objects.get(pk=attempt)
        self.assertEqual(previous.reason, "worker_lost")
        event = Outbox.objects.get(job=self.job, kind="video.transcode.v1", attempt_no=2)
        attempt = begin_attempt(self.job.id, 1, 2, event.payload)
        finish_attempt(attempt, "success", event.payload)
        self.assertIsNone(begin_attempt(self.job.id, 1, 2, event.payload))
        self.assertEqual(self.job.attempts.count(), 2)
        self.video.refresh_from_db()
        self.assertEqual(self.video.state, Video.State.QUEUED)

    def test_cancelled_delivery_cannot_start_or_recover_attempt(self):
        cancelled = Event()
        cancelled.set()
        self.assertIsNone(begin_attempt(self.job.id, 1, 1, self.payload, cancelled=cancelled))
        self.assertFalse(self.job.attempts.exists())

        attempt = begin_attempt(self.job.id, 1, 1, self.payload)
        self.assertIsNone(begin_attempt(self.job.id, 1, 1, self.payload, cancelled=cancelled))
        self.assertEqual(Attempt.objects.get(pk=attempt).state, Attempt.State.RUNNING)
        self.assertFalse(Outbox.objects.filter(job=self.job).exists())
