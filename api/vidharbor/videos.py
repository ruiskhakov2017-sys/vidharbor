from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .accounts import error, json_object, require_author
from .models import Attempt, Job, Video
from .public_videos import watch_url
from .reprocessing import RetryRejected, retry_advice, retry_original


def cabinet_videos():
    job_state = Job.objects.filter(
        video_id=OuterRef("pk"), generation=OuterRef("generation"),
    ).values("state")
    job_id = Job.objects.filter(video_id=OuterRef("pk"), generation=OuterRef("generation")).values("id")
    reason = Attempt.objects.filter(
        job__video_id=OuterRef("pk"), job__generation=OuterRef("generation"),
    ).order_by("-number").values("reason")
    failure_state = Attempt.objects.filter(
        job__video_id=OuterRef("pk"), job__generation=OuterRef("generation"),
    ).order_by("-number").values("state")
    return Video.objects.annotate(
        job_state=Subquery(job_state), job_id=Subquery(job_id), failure_reason=Subquery(reason[:1]),
        failure_state=Subquery(failure_state[:1]),
    )


def cabinet_item(video):
    item = {"id": video.id, "title": video.title, "state": video.state}
    if video.state == Video.State.READY and video.published_generation is not None:
        item.update(state=Video.State.READY, watch_url=watch_url(video.id))
    elif video.job_state in (Job.State.PROCESSING, Job.State.RETRY_WAIT, Job.State.FAILED):
        item["state"] = video.job_state
    if video.job_state == Job.State.FAILED:
        reason = video.failure_reason if video.failure_state == Attempt.State.FAILED else ""
        allowed, advice = retry_advice(video.state, video.job_state, reason)
        item.update(job_id=video.job_id, can_retry=allowed, retry_advice=advice)
    return item


@require_POST
@never_cache
@require_author
def rename(request, video_id):
    with transaction.atomic():
        video = get_object_or_404(
            Video.objects.select_for_update(), id=video_id, owner=request.user,
        )
        if video.state in (Video.State.DELETING, Video.State.DELETED):
            return error("Видео удалено или удаляется.", 409)
        title = json_object(request).get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or "\0" in title:
            return error("Введите название длиной от 1 до 200 символов.", 400)
        video.title = title.strip()
        video.save(update_fields=["title"])
        return JsonResponse(cabinet_item(cabinet_videos().get(pk=video.pk)))


@require_POST
@never_cache
@require_author
def retry(request, video_id, failed_job_id):
    source = get_object_or_404(Job, pk=failed_job_id, video_id=video_id, video__owner=request.user)
    try:
        job, created = retry_original(source)
    except RetryRejected as exc:
        response = error(str(exc), exc.status)
        if exc.status in (423, 503):
            response["Retry-After"] = "1"
        return response
    return JsonResponse({
        "job": {"id": job.id, "generation": job.generation, "state": job.state, "retry_of": job.retry_of_id},
        "video": cabinet_item(cabinet_videos().get(pk=source.video_id)),
    }, status=201 if created else 200)


@require_POST
@never_cache
@require_author
def delete(request, video_id):
    from .deletion import revoke, clean
    get_object_or_404(Video, pk=video_id, owner=request.user)
    try:
        revoke(video_id)
        clean(video_id)
    except OSError:
        # A committed intent already revokes access, even if cleanup must resume.
        video = Video.objects.get(pk=video_id)
        if video.state not in (Video.State.DELETING, Video.State.DELETED):
            return error("Журнал удаления временно недоступен. Повторите запрос.", 503)
    video = Video.objects.get(pk=video_id)
    return JsonResponse({"id": video.id, "state": video.state}, status=200 if video.state == Video.State.DELETED else 202)
