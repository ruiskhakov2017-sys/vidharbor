from django.urls import path

from . import accounts, health, public_videos, uploads, videos

urlpatterns = [
    path("health/live", health.live, name="live"),
    path("health/ready", health.ready, name="ready"),
    path("api/auth/session", accounts.session),
    path("api/auth/register", accounts.register),
    path("api/auth/login", accounts.sign_in),
    path("api/auth/logout", accounts.sign_out),
    path("api/videos", uploads.videos),
    path("api/videos/<uuid:video_id>/rename", videos.rename),
    path("api/videos/<uuid:video_id>/delete", videos.delete),
    path("api/videos/<uuid:video_id>/jobs/<uuid:failed_job_id>/retry", videos.retry),
    path("api/catalog", public_videos.catalog),
    path("api/catalog/<uuid:video_id>", public_videos.detail),
    path("watch/<uuid:video_id>", public_videos.watch),
    path("media/<uuid:video_id>/master.m3u8", public_videos.master),
    path("media/<uuid:video_id>/g<int:generation>/<str:variant>/<str:filename>", public_videos.rendition),
    path("api/uploads", uploads.create_upload),
    path("api/uploads/<uuid:upload_id>", uploads.upload_info),
    path("api/uploads/<uuid:upload_id>/content", uploads.receive_upload),
    path("api/uploads/<uuid:upload_id>/complete", uploads.complete_upload),
]
