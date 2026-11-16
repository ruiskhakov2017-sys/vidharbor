"""Read/verify a paused stand and reconcile a fenced restore. No database deletion."""
import hashlib
import json
import sys

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from vidharbor import journal
from vidharbor.deletion import apply_journal
from vidharbor.models import Attempt, Delivery, Job, Outbox, ReadyEvent, ReceiverRow, SheetAllocator, Upload, Video
from vidharbor.publication import ready_path, verify_manifest
from vidharbor.storage import file_digest


def describe():
    files = {}
    for path in settings.MEDIA_ROOT.rglob("*"):
        if path.is_symlink():
            raise CommandError("Media contains a symlink; snapshot refused")
        if path.is_file():
            files[path.relative_to(settings.MEDIA_ROOT).as_posix()] = {
                "bytes": path.stat().st_size, "sha256": file_digest(path)}
    models = {}
    for model in (Upload, Video, Job, Attempt, Outbox, ReadyEvent, Delivery, ReceiverRow, SheetAllocator):
        models[model.__name__] = list(model.objects.order_by(model._meta.pk.name).values())
    _, head = journal.read_entries()
    return {"files": files, "models": models, "journal_head": head}


@transaction.atomic
def reconcile(apply=False):
    if not settings.RESTORE_MODE:
        raise CommandError("Reconciliation requires RESTORE_MODE=1 and fenced old writers")
    entries, head = journal.read_entries()
    deleted = {e["key"] for e in entries if e["kind"] == "delete"}
    if Video.objects.filter(pk__in=deleted).exclude(state="DELETED").exists():
        raise CommandError("Apply deletion authority before reconciling jobs")
    actions = []
    for video in Video.objects.filter(state="READY"):
        job = Job.objects.get(video=video, generation=video.published_generation, state="SUCCEEDED")
        attempt = job.attempts.get(state="SUCCEEDED")
        verify_manifest(ready_path(video.id, video.published_generation), job, attempt.pk)
    for job in Job.objects.filter(state__in=["QUEUED", "PROCESSING", "RETRY_WAIT"]).exclude(
            video__state__in=["DELETING", "DELETED"]).select_related("video"):
        if job.generation != job.video.generation or job.video.state != "QUEUED":
            raise CommandError("Unaccounted active obsolete generation")
        message = Outbox.objects.filter(job=job, kind="video.transcode.v1").order_by("-attempt_no").first()
        if message is None:
            raise CommandError("Active job has no durable command")
        actions.append({"job_id": str(job.id), "message_id": str(message.pk),
                        "action": "redeliver_same_command", "attempts_preserved": job.attempts.count()})
        if apply:
            # Scoped at-least-once recovery of a proved obligation, never all outbox.
            # Duplicates in the cold broker copy keep the same message ID.
            Outbox.objects.filter(pk=message.pk).update(sent_at=None)
    reservations = {e["key"]: e["data"] for e in entries if e["kind"] == "sheet"}
    for event in ReadyEvent.objects.select_related("source__job"):
        reservation = reservations.get(str(event.pk))
        if reservation is None or reservation["row"] != event.sheet_row:
            raise CommandError("Unaccounted sheet reservation: notifier must remain closed")
        source = event.source
        if (source is None or source.pk != event.pk or source.kind != "video.ready"
                or source.job.video_id != event.video_id or source.job.generation != event.generation
                or event.payload != {**source.payload, "sheet_row": event.sheet_row}
                or reservation.get("snapshot_sha256") != hashlib.sha256(journal.canonical(source.payload)).hexdigest()):
            raise CommandError("Restored READY snapshot conflicts with its source or reservation")
    high = max((r["row"] + 1 for r in reservations.values()), default=2)
    if apply:
        with transaction.atomic():
            SheetAllocator.objects.get_or_create(pk=1)
            allocator = SheetAllocator.objects.select_for_update().get(pk=1)
            allocator.next_row = max(allocator.next_row, high)
            allocator.save(update_fields=["next_row"])
    return {"actions": actions, "journal_head": head, "allocator_floor": high,
            "applied": apply, "external_sheets": "Not Run; notifier stays disabled"}


class Command(BaseCommand):
    help = "Inspect/verify a paused stand or reconcile an isolated restore."

    def add_arguments(self, parser):
        parser.add_argument("action", choices=["describe", "journal", "verify", "apply-deletions", "reconcile"])
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        action = options["action"]
        if action == "describe":
            result = describe()
        elif action == "journal":
            entries, head = journal.read_entries()
            result = {"entries": entries, "head": head}
        elif action == "verify":
            expected = json.load(sys.stdin)
            actual = json.loads(json.dumps(describe(), default=str))
            if actual["files"] != expected["files"] or actual["models"] != expected["models"]:
                raise CommandError("Restored files or database records differ from snapshot")
            result = {"verified_files": len(actual["files"]),
                      "verified_records": {k: len(v) for k, v in actual["models"].items()}}
        elif action == "apply-deletions":
            if not settings.RESTORE_MODE:
                raise CommandError("Apply deletions only while restore access is closed")
            result = {"journal_head": apply_journal()}
        else:
            result = reconcile(options["apply"])
        self.stdout.write(json.dumps(result, default=str, ensure_ascii=False))
