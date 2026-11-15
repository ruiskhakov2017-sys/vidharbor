"""Revoke first, then clean under the stable S04 lock; safe to resume after a crash."""
import logging
import shutil
import time
import uuid
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from . import journal
from .models import Attempt, Job, Video
from .storage import media_lock, original_path, part_path, sync_directory

logger = logging.getLogger(__name__)


def revoke(video_id):
    with transaction.atomic():
        video = Video.objects.select_for_update().get(pk=video_id)
        if video.state in (Video.State.DELETING, Video.State.DELETED):
            return video
        # Journal survives rollback/restore of PostgreSQL. No confirmed deletion
        # without this durable intent. Readers also consult it after a DB rollback.
        existing = journal.deletion_record(video.id)
        data = existing["data"] if existing else {
            "video_id": str(video.id), "upload_id": str(video.upload_id),
            "deletion_id": str(uuid.uuid4()), "generation": video.generation,
            "deleted_at": timezone.now().isoformat()}
        journal.append("delete", video.id, data)
        video.state = Video.State.DELETING
        video.generation += 1
        video.published_generation = None
        video.deleted_at = datetime.fromisoformat(data["deleted_at"]) if "deleted_at" in data else timezone.now()
        video.save(update_fields=["state", "generation", "published_generation", "deleted_at"])
        jobs = Job.objects.filter(video=video).exclude(state__in=[Job.State.SUCCEEDED, Job.State.FAILED, Job.State.CANCELLED])
        Attempt.objects.filter(job__in=jobs, state=Attempt.State.RUNNING).update(
            state=Attempt.State.FAILED, reason="video_deleted", finished_at=timezone.now())
        jobs.update(state=Job.State.CANCELLED)
        # Imported lazily so the first S05 migration remains independent of S06.
        from .notifications import cancel_locked
        cancel_locked(video)
        return video


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def clean(video_id):
    video = Video.objects.get(pk=video_id)
    if video.state == Video.State.DELETED:
        return True
    if video.state != Video.State.DELETING:
        return False
    # No DB transaction is held while waiting for a live encoder's lock.
    with media_lock(video.id) as acquired:
        if not acquired:
            return False
        from .publication import staging_path, ready_path
        paths = [original_path(video.upload_id), part_path(video.upload_id), staging_path(video.id),
                 ready_path(video.id, 1).parent]
        for path in paths:
            remove(path)
            if path.parent.exists():
                sync_directory(path.parent)
        with transaction.atomic():
            video = Video.objects.select_for_update().get(pk=video_id)
            if video.state == Video.State.DELETING:
                video.state = Video.State.DELETED
                video.save(update_fields=["state"])
    return True


def clean_pending():
    count = 0
    for video_id in Video.objects.filter(state=Video.State.DELETING).values_list("id", flat=True).iterator():
        try:
            count += clean(video_id)
        except OSError:
            logger.exception("Video cleanup will resume", extra={"video_id": str(video_id)})
    return count


def apply_journal():
    entries, head = journal.read_entries()
    for entry in entries:
        if entry["kind"] == "delete" and Video.objects.filter(pk=entry["key"]).exists():
            revoke(entry["key"])
    clean_pending()
    return head


class Revocation:
    """A live FFmpeg sees deletion within one second, including in-flight work."""
    def __init__(self, broker_cancelled, video_id, generation):
        self.broker = broker_cancelled
        self.video_id, self.generation = video_id, generation
        self.checked, self.revoked = 0, False

    def is_set(self):
        if self.broker.is_set() or self.revoked:
            return True
        if time.monotonic() - self.checked >= 1:
            self.checked = time.monotonic()
            self.revoked = not Video.objects.filter(pk=self.video_id, generation=self.generation,
                                                   state=Video.State.QUEUED).exists()
            self.revoked = self.revoked or journal.is_deleted(self.video_id)
        return self.revoked

    def wait(self, timeout):
        if self.broker.wait(timeout):
            return True
        return self.is_set()
