import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class Upload(models.Model):
    class State(models.TextChoices):
        UPLOADING = "UPLOADING"
        VERIFYING = "VERIFYING"
        COMPLETE = "COMPLETE"
        INTERRUPTED = "INTERRUPTED"
        EXPIRED = "EXPIRED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    expected_size = models.PositiveBigIntegerField()
    size = models.PositiveBigIntegerField(null=True)
    sha256 = models.CharField(max_length=64, blank=True)
    state = models.CharField(max_length=12, choices=State, default=State.UPLOADING)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["state", "updated_at"])]


class Video(models.Model):
    class State(models.TextChoices):
        QUEUED = "QUEUED"
        READY = "READY"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    upload = models.OneToOneField(Upload, on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=200)
    generation = models.PositiveIntegerField(default=1)
    state = models.CharField(max_length=12, choices=State, default=State.QUEUED)
    created_at = models.DateTimeField(auto_now_add=True)
    published_generation = models.PositiveIntegerField(null=True)
    ready_at = models.DateTimeField(null=True)


class Job(models.Model):
    class State(models.TextChoices):
        QUEUED = "QUEUED"
        PROCESSING = "PROCESSING"
        RETRY_WAIT = "RETRY_WAIT"
        FAILED = "FAILED"
        SUCCEEDED = "SUCCEEDED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    video = models.ForeignKey(Video, on_delete=models.PROTECT)
    generation = models.PositiveIntegerField()
    state = models.CharField(max_length=12, choices=State, default=State.QUEUED)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["video", "generation"], name="unique_video_job")
        ]


class Attempt(models.Model):
    class State(models.TextChoices):
        RUNNING = "RUNNING"
        SUCCEEDED = "SUCCEEDED"
        RETRY = "RETRY"
        FAILED = "FAILED"

    job = models.ForeignKey(Job, on_delete=models.PROTECT, related_name="attempts")
    number = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=12, choices=State)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True)
    reason = models.CharField(max_length=80, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["job", "number"], name="unique_job_attempt")]


class Outbox(models.Model):
    message_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(Job, on_delete=models.PROTECT)
    kind = models.CharField(max_length=40)
    attempt_no = models.PositiveIntegerField()
    payload = models.JSONField()
    due_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["job", "kind", "attempt_no"], name="unique_outbox_attempt"
            )
        ]
