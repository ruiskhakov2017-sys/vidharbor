from django.db import transaction
from django.db.models import OuterRef, Subquery
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .accounts import error, json_object, require_author
from .models import Job, Video
from .public_videos import watch_url


def cabinet_videos():
    job_state = Job.objects.filter(
        video_id=OuterRef("pk"), generation=OuterRef("generation"),
    ).values("state")
    return Video.objects.annotate(job_state=Subquery(job_state))


def cabinet_item(video):
    item = {"id": video.id, "title": video.title, "state": Video.State.QUEUED}
    if video.state == Video.State.READY and video.published_generation is not None:
        item.update(state=Video.State.READY, watch_url=watch_url(video.id))
    elif video.job_state in (Job.State.PROCESSING, Job.State.RETRY_WAIT, Job.State.FAILED):
        item["state"] = video.job_state
    return item


@require_POST
@never_cache
@require_author
def rename(request, video_id):
    with transaction.atomic():
        video = get_object_or_404(
            Video.objects.select_for_update(), id=video_id, owner=request.user,
        )
        title = json_object(request).get("title")
        if not isinstance(title, str) or not 1 <= len(title.strip()) <= 200 or "\0" in title:
            return error("Введите название длиной от 1 до 200 символов.", 400)
        video.title = title.strip()
        video.save(update_fields=["title"])
        return JsonResponse(cabinet_item(cabinet_videos().get(pk=video.pk)))
