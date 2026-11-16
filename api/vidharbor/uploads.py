import errno
import hashlib
import os
import shutil
import uuid

from django.conf import settings
from django.db import transaction
from django.http import JsonResponse, UnreadablePostError
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .accounts import error, json_object, require_author
from .media_probe import InvalidVideo, check_video
from .models import Job, Outbox, Upload, Video
from .storage import (
    CHUNK_SIZE,
    DISK_RESERVE,
    file_digest,
    media_lock,
    original_path,
    part_path,
    sync_directory,
)
from .videos import cabinet_item, cabinet_videos
from . import telemetry


class IncompleteBody(ValueError):
    pass


def owned_upload(request, upload_id):
    return get_object_or_404(Upload, id=upload_id, owner=request.user)


def describe_upload(upload):
    return {"id": upload.id, "title": upload.title, "state": upload.state}


@require_GET
@never_cache
@require_author
def videos(request):
    videos = cabinet_videos().filter(owner=request.user).exclude(state=Video.State.DELETED).order_by("-created_at")
    return JsonResponse({"videos": [cabinet_item(video) for video in videos]})


@require_GET
@never_cache
@require_author
def upload_info(request, upload_id):
    return JsonResponse(describe_upload(owned_upload(request, upload_id)))


def has_space(size):
    return shutil.disk_usage(settings.MEDIA_ROOT).free >= size + DISK_RESERVE


@require_POST
@require_author
def create_upload(request):
    data = json_object(request)
    title, size = data.get("title"), data.get("size")
    if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or "\0" in title:
        return error("Введите название длиной от 1 до 200 символов.", 400)
    if type(size) is not int or size < 1:
        return error("Выберите непустой файл.", 400)
    if size > settings.MAX_UPLOAD_BYTES:
        return error("Размер файла превышает лимит загрузки.", 413)
    if not has_space(size):
        return error("На диске недостаточно места для загрузки.", 507)
    upload = Upload.objects.create(owner=request.user, title=title.strip(), expected_size=size)
    telemetry.identify(upload_id=upload.id, correlation_id=upload.id)
    telemetry.log_event("upload.created", upload_id=upload.id)
    return JsonResponse(describe_upload(upload), status=201)


def set_interrupted(upload):
    upload.state = Upload.State.INTERRUPTED
    upload.save(update_fields=["state", "updated_at"])


def write_content(request, destination, size):
    digest = hashlib.sha256()
    received = 0
    with destination.open("xb") as output:
        while received < size:
            try:
                block = request.read(min(CHUNK_SIZE, size - received))
            except (UnreadablePostError, OSError) as exc:
                raise IncompleteBody from exc
            if not block:
                raise IncompleteBody
            output.write(block)
            received += len(block)
            digest.update(block)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(destination.parent)
    return received, digest.hexdigest()


@require_http_methods(["PUT"])
@require_author
def receive_upload(request, upload_id):
    upload = owned_upload(request, upload_id)
    if request.content_type != "application/octet-stream":
        return error("Передайте файл как двоичный поток.", 415)
    try:
        length = int(request.META.get("CONTENT_LENGTH", ""))
    except ValueError:
        return error("Укажите длину передачи.", 411)
    if length > settings.MAX_UPLOAD_BYTES:
        return error("Размер файла превышает лимит загрузки.", 413)
    if length != upload.expected_size:
        return error("Длина передачи не совпадает с размером загрузки.", 400)

    with media_lock("active-transfer") as available, media_lock(upload_id) as acquired:
        if not available or not acquired:
            return error("Другая загрузка уже выполняется. Повторите позже.", 409)
        upload.refresh_from_db()
        destination = part_path(upload_id)
        if upload.state != Upload.State.UPLOADING or destination.exists():
            return error("Начните новую передачу файла.", 409)
        if not has_space(length):
            return error("На диске недостаточно места для загрузки.", 507)
        try:
            upload.size, upload.sha256 = write_content(request, destination, length)
        except IncompleteBody:
            set_interrupted(upload)
            return error("Передача файла прервалась. Начните загрузку заново.", 400)
        except OSError as exc:
            set_interrupted(upload)
            if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                return error("На диске закончилось место.", 507)
            raise
        upload.state = Upload.State.VERIFYING
        upload.save(update_fields=["size", "sha256", "state", "updated_at"])
        return JsonResponse(describe_upload(upload))


def completed_result(job):
    message = Outbox.objects.get(job=job, kind="video.transcode.v1", attempt_no=1)
    return JsonResponse(
        {"video_id": job.video_id, "job_id": job.id, "message_id": message.message_id}
    )


@telemetry.traced("job.create")
@transaction.atomic
def queue_video(upload):
    video = Video.objects.create(upload=upload, owner=upload.owner, title=upload.title)
    job = Job.objects.create(video=video, generation=video.generation)
    message_id = uuid.uuid4()
    telemetry.identify(upload_id=upload.id, correlation_id=upload.id, video_id=video.id, job_id=job.id, message_id=message_id)
    Outbox.objects.create(
        message_id=message_id,
        job=job,
        kind="video.transcode.v1",
        attempt_no=1,
        payload={
            "version": 1,
            "message_id": str(message_id),
            "job_id": str(job.id),
            "video_id": str(video.id),
            "generation": job.generation,
            "next_attempt_no": 1,
            "request_id": str(upload.id),
            "trace_context": telemetry.current_context(),
        },
    )
    upload.state = Upload.State.COMPLETE
    upload.save(update_fields=["state", "updated_at"])
    telemetry.log_event("job.created")
    return job


@require_POST
@require_author
def complete_upload(request, upload_id):
    upload = owned_upload(request, upload_id)
    with media_lock(upload_id) as acquired:
        if not acquired:
            response = error("Загрузка уже проверяется. Повторите запрос.", 409)
            response["Retry-After"] = "1"
            return response
        upload.refresh_from_db()
        if upload.state == Upload.State.COMPLETE:
            return completed_result(Job.objects.get(video=upload.video, generation=1))
        if upload.state != Upload.State.VERIFYING:
            return error("Передача не завершена. Начните загрузку заново.", 409)

        original = original_path(upload_id)
        part = part_path(upload_id)
        source = original if original.exists() else part
        try:
            if source.stat().st_size != upload.size or file_digest(source) != upload.sha256:
                raise InvalidVideo("Размер или контрольная сумма файла не совпадает.")
            check_video(source)
        except (InvalidVideo, FileNotFoundError) as exc:
            set_interrupted(upload)
            message = str(exc) if isinstance(exc, InvalidVideo) else "Файл загрузки не найден."
            return error(message, 422)

        if source == part:
            os.replace(part, original)
        sync_directory(original.parent)
        sync_directory(part.parent)
        return completed_result(queue_video(upload))
