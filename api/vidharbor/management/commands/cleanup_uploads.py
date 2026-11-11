import time
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone

from vidharbor.models import Job, Upload
from vidharbor.storage import media_lock, original_path, part_path


STALE_STATES = [Upload.State.UPLOADING, Upload.State.VERIFYING, Upload.State.INTERRUPTED]


def expire_uploads():
    cutoff = timezone.now() - timedelta(hours=24)
    stale = Upload.objects.filter(state__in=STALE_STATES, updated_at__lt=cutoff)
    expired = 0
    for upload_id in stale.values_list("id", flat=True).iterator():
        with media_lock(upload_id) as acquired:
            if not acquired:
                continue
            if not stale.filter(id=upload_id).exists():
                continue
            if Job.objects.filter(video__upload_id=upload_id).exists():
                continue
            part_path(upload_id).unlink(missing_ok=True)
            original_path(upload_id).unlink(missing_ok=True)
            stale.filter(id=upload_id).update(state=Upload.State.EXPIRED, updated_at=timezone.now())
            expired += 1
    return expired


class Command(BaseCommand):
    help = "Expire abandoned uploads older than 24 hours."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")

    def handle(self, *args, **options):
        while True:
            close_old_connections()
            self.stdout.write(f"Expired uploads: {expire_uploads()}")
            if not options["watch"]:
                return
            time.sleep(300)
