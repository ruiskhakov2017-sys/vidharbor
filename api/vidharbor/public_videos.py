import re

from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_safe

from .models import Video
from .publication import SEGMENT, master_url


def published_videos():
    return Video.objects.filter(state=Video.State.READY, published_generation__isnull=False)


def watch_url(video_id):
    return f"/watch/{video_id}"


def describe(video):
    return {"id": video.id, "title": video.title, "watch_url": watch_url(video.id), "playlist_url": master_url(video.id)}


@require_GET
@never_cache
def catalog(request):
    title = request.GET.get("q", "").strip()[:200]
    if "\0" in title:
        return JsonResponse({"videos": []})
    videos = published_videos().order_by("-ready_at", "id")
    if title:
        videos = videos.filter(title__icontains=title)
    return JsonResponse({"videos": [describe(video) for video in videos]})


@require_GET
@never_cache
def detail(request, video_id):
    video = published_videos().filter(pk=video_id).first()
    if video is None:
        return JsonResponse({"error": "Видео недоступно."}, status=404)
    return JsonResponse(describe(video))


def internal_file(video_id, generation, filename):
    content_type = "video/mp2t" if filename.endswith(".ts") else "application/vnd.apple.mpegurl"
    response = HttpResponse(content_type=content_type)
    response["X-Accel-Redirect"] = f"/_vidharbor_hls/{video_id}/g{generation}/{filename}"
    return response


@require_safe
@never_cache
def watch(request, video_id):
    get_object_or_404(published_videos(), pk=video_id)
    response = HttpResponse()
    response["X-Accel-Redirect"] = "/_vidharbor_watch"
    return response


@require_safe
@never_cache
def master(request, video_id):
    video = get_object_or_404(published_videos(), pk=video_id)
    return internal_file(video_id, video.published_generation, "master.m3u8")


@require_safe
@never_cache
def rendition(request, video_id, generation, variant, filename):
    if not re.fullmatch(r"v[0-2]", variant) or (
        filename != "index.m3u8" and not re.fullmatch(SEGMENT, filename)
    ):
        raise Http404
    get_object_or_404(published_videos(), pk=video_id, published_generation=generation)
    return internal_file(video_id, generation, f"{variant}/{filename}")
