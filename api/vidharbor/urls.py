from django.urls import path

from . import health

urlpatterns = [
    path("health/live", health.live, name="live"),
    path("health/ready", health.ready, name="ready"),
]
