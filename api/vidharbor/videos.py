from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .accounts import error, json_object, require_author
from .models import Video


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
    return JsonResponse({"id": video.id, "title": video.title, "state": video.state})
