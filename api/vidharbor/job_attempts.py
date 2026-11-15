import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Attempt, Job, Outbox, Video


MAX_ATTEMPTS = 3
RETRY_DELAYS = (30, 120)


def queue_retry(job, previous, original_payload):
    number = previous.number + 1
    message_id = uuid.uuid4()
    payload = dict(original_payload)
    payload.update(message_id=str(message_id), next_attempt_no=number)
    Outbox.objects.create(
        message_id=message_id,
        job=job,
        kind="video.transcode.v1",
        attempt_no=number,
        payload=payload,
        due_at=timezone.now() + timedelta(seconds=RETRY_DELAYS[previous.number - 1]),
    )
    job.state = Job.State.RETRY_WAIT
    job.save(update_fields=["state"])


def queue_terminal(job, attempt, reason):
    message_id = uuid.uuid4()
    Outbox.objects.create(
        message_id=message_id,
        job=job,
        kind="video.failed.v1",
        attempt_no=attempt.number,
        payload={
            "version": 1,
            "message_id": str(message_id),
            "terminal_event_id": str(message_id),
            "job_id": str(job.id),
            "video_id": str(job.video_id),
            "generation": job.generation,
            "attempt_no": attempt.number,
            "reason": reason,
        },
    )
    job.state = Job.State.FAILED
    job.save(update_fields=["state"])


@transaction.atomic
def begin_attempt(job_id, generation, number, payload, *, cancelled=None):
    video_id = Job.objects.values_list("video_id", flat=True).get(pk=job_id)
    video = Video.objects.select_for_update().get(pk=video_id)
    job = Job.objects.select_for_update().select_related("video").get(pk=job_id)
    if video.state != Video.State.QUEUED:
        return None
    if generation != job.generation or generation != job.video.generation:
        return None
    if job.state in (Job.State.SUCCEEDED, Job.State.FAILED, Job.State.CANCELLED):
        return None
    previous = job.attempts.order_by("-number").first()
    if cancelled is not None and cancelled.is_set():
        return None
    if previous and previous.state == Attempt.State.RUNNING:
        previous.state = Attempt.State.RETRY if previous.number < MAX_ATTEMPTS else Attempt.State.FAILED
        previous.reason = "worker_lost"
        previous.finished_at = timezone.now()
        previous.save(update_fields=["state", "reason", "finished_at"])
        if previous.number < MAX_ATTEMPTS:
            queue_retry(job, previous, payload)
        else:
            queue_terminal(job, previous, "worker_lost")
        return None
    expected = previous.number + 1 if previous else 1
    if number != expected or number > MAX_ATTEMPTS:
        return None
    attempt = Attempt.objects.create(job=job, number=number, state=Attempt.State.RUNNING)
    job.state = Job.State.PROCESSING
    job.save(update_fields=["state"])
    return attempt.pk


@transaction.atomic
def finish_attempt(attempt_id, outcome, payload, reason="", *, publish=None):
    attempt = Attempt.objects.select_related("job").get(pk=attempt_id)
    Video.objects.select_for_update().get(pk=attempt.job.video_id)
    job = Job.objects.select_for_update().get(pk=attempt.job_id)
    attempt.refresh_from_db()
    if attempt.state != Attempt.State.RUNNING:
        return
    attempt.finished_at = timezone.now()
    attempt.reason = reason
    if outcome == "success" and publish is not None and not publish(job):
        job.state = Job.State.FAILED
        job.save(update_fields=["state"])
        attempt.state = Attempt.State.FAILED
        attempt.reason = "obsolete_generation"
        attempt.save(update_fields=["state", "reason", "finished_at"])
        return
    if outcome == "success":
        attempt.state = Attempt.State.SUCCEEDED
        job.state = Job.State.SUCCEEDED
        job.save(update_fields=["state"])
    elif outcome == "retry" and attempt.number < MAX_ATTEMPTS:
        attempt.state = Attempt.State.RETRY
        queue_retry(job, attempt, payload)
    else:
        attempt.state = Attempt.State.FAILED
        queue_terminal(job, attempt, reason)
    attempt.save(update_fields=["state", "reason", "finished_at"])


def execute_attempt(job, payload, lock_fd, cancelled, processor, *, publish=None):
    attempt = begin_attempt(
        job.id, payload["generation"], payload["next_attempt_no"], payload, cancelled=cancelled,
    )
    if attempt is not None:
        outcome, reason = processor(job, attempt, lock_fd)
        finish_attempt(attempt, outcome, payload, reason, publish=publish)
