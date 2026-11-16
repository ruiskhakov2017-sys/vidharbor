"""A manual retry is a durable successor of one specific failed job."""

import uuid
from . import telemetry

from django.db import transaction

from .media_probe import InvalidVideo, ProbeTimeout, check_video
from .models import Attempt, Job, Outbox, Upload, Video
from .storage import file_digest, media_lock, original_path


REPAIRABLE = frozenset({
    "worker_lost", "channel_lost", "media_io", "media_unavailable", "disk_full", "ffmpeg_timeout", "probe_timeout",
})
NEW_SOURCE = "Оригинал повреждён или не поддерживается. Загрузите исправный файл."


class RetryRejected(ValueError):
    def __init__(self, message, status=409):
        super().__init__(message)
        self.status = status


def retry_advice(video_state, job_state, reason):
    if video_state != Video.State.QUEUED or job_state != Job.State.FAILED:
        return False, "Повтор доступен только после окончательного сбоя обработки."
    if reason not in REPAIRABLE:
        return False, NEW_SOURCE if reason in {"invalid_video", "ffmpeg_decode_or_encode"} else (
            "Причина сбоя требует диагностики. Повтор обработки пока недоступен."
        )
    return True, "Устраните причину сбоя и повторите обработку сохранённого оригинала."


def existing_retry(source):
    return Job.objects.filter(retry_of=source).first()


def validate_source(video):
    upload = video.upload
    path = original_path(upload.id)
    try:
        if not path.exists():
            raise RetryRejected("Оригинал не найден. Загрузите исправный файл.", 422)
        if (path.is_symlink() or not path.is_file() or upload.state != Upload.State.COMPLETE
                or path.stat().st_size != upload.size or file_digest(path) != upload.sha256):
            raise RetryRejected(NEW_SOURCE, 422)
        check_video(path)
    except ProbeTimeout as exc:
        raise RetryRejected("Проверка оригинала не завершилась вовремя. Повторите запрос позже.", 503) from exc
    except InvalidVideo as exc:
        raise RetryRejected(NEW_SOURCE, 422) from exc
    except FileNotFoundError as exc:
        if exc.filename == "ffprobe":
            raise RetryRejected("Проверка оригинала временно недоступна. Повторите запрос позже.", 503) from exc
        raise RetryRejected("Оригинал не найден. Загрузите исправный файл.", 422) from exc
    except OSError as exc:
        raise RetryRejected("Оригинал временно недоступен. Проверьте хранилище и повторите запрос.", 503) from exc


def require_failed_source(video, source):
    if source.generation != video.generation:
        raise RetryRejected("Это задание больше не является текущим. Обновите кабинет.")
    attempt = source.attempts.order_by("-number").first()
    if attempt is None or attempt.state != Attempt.State.FAILED:
        raise RetryRejected("История сбоя требует диагностики. Повтор обработки пока недоступен.")
    allowed, message = retry_advice(video.state, source.state, attempt.reason if attempt else "")
    if not allowed:
        raise RetryRejected(message)


@telemetry.traced("job.retry")
def retry_original(source):
    # A receipt lookup precedes current-generation/state checks. Replays never start work.
    video = Video.objects.get(pk=source.video_id)
    telemetry.identify(correlation_id=video.upload_id, upload_id=video.upload_id, video_id=video.id)
    from .journal import is_deleted
    if is_deleted(video.id):
        raise RetryRejected("Видео удалено или удаляется.")
    if video.state not in (Video.State.QUEUED, Video.State.READY):
        raise RetryRejected("Видео удаляется или недоступно для обработки.")
    previous = existing_retry(source)
    if previous is not None:
        return previous, False
    require_failed_source(video, source)
    # Same stable per-video lock as S04. Never wait for it while holding DB row locks.
    with media_lock(video.id, timeout=5) as acquired:
        if not acquired:
            previous = existing_retry(source)
            if previous is not None:
                return previous, False
            raise RetryRejected("Файл занят. Повторите тот же запрос позже.", 423)
        video.refresh_from_db()
        source.refresh_from_db()
        previous = existing_retry(source)
        if previous is not None:
            return previous, False
        require_failed_source(video, source)
        validate_source(video)  # Reading/hash/ffprobe are outside the short DB transaction.
        with transaction.atomic():
            video = Video.objects.select_for_update().get(pk=video.pk)
            source = Job.objects.select_for_update().get(pk=source.pk)
            if is_deleted(video.id):
                raise RetryRejected("Видео удалено или удаляется.")
            previous = existing_retry(source)
            if previous is not None:
                return previous, False
            require_failed_source(video, source)
            video.generation += 1
            video.state = Video.State.QUEUED
            video.published_generation = None
            video.ready_at = None
            video.save(update_fields=["generation", "state", "published_generation", "ready_at"])
            job = Job.objects.create(video=video, generation=video.generation, retry_of=source)
            message_id = uuid.uuid4()
            Outbox.objects.create(
                message_id=message_id, job=job, kind="video.transcode.v1", attempt_no=1,
                payload={
                    "version": 1, "message_id": str(message_id), "job_id": str(job.id),
                    "video_id": str(video.id), "generation": job.generation, "next_attempt_no": 1,
                    "request_id": str(source.id), "trace_context": telemetry.current_context(),
                },
            )
            return job, True
