"""Independent delivery of existing S04 snapshots; never creates a transcode job."""
import hashlib
import hmac
import json
import uuid
from http.client import HTTPException
from datetime import datetime, timedelta, timezone as dt_timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from django.db import transaction
from django.db.models import Q, F
from django.conf import settings
from django.utils import timezone

from .models import Delivery, ReadyEvent, Video, Outbox, SheetAllocator
from . import journal
from .webhook_transport import PinnedHTTPSHandler

OFFSETS = (0, 60, 300, 900, 2700)
LEASE = timedelta(seconds=60)
WINDOW = timedelta(hours=1)


def validate_payload(payload):
    if set(payload) != {"type", "version", "event_id", "video_id", "title",
                        "ready_at", "playlist_url", "sheet_row"}:
        raise ValueError("invalid_fields")
    if payload["type"] != "video.ready" or type(payload["version"]) is not int or payload["version"] != 1:
        raise ValueError("invalid_version")
    for field in ("event_id", "video_id"):
        uuid.UUID(payload[field])
    if type(payload["sheet_row"]) is not int or payload["sheet_row"] < 2:
        raise ValueError("invalid_row")
    if not isinstance(payload["title"], str) or not 1 <= len(payload["title"]) <= 200:
        raise ValueError("invalid_title")
    ready = datetime.fromisoformat(payload["ready_at"].replace("Z", "+00:00"))
    if ready.utcoffset() != timedelta(0):
        raise ValueError("invalid_time")
    url = urlsplit(payload["playlist_url"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("invalid_playlist")
    if not url.path.endswith("/master.m3u8"):
        raise ValueError("invalid_playlist")


def encode_event(event):
    payload = event.payload
    validate_payload(payload)
    if (payload["event_id"] != str(event.pk)
            or payload["video_id"] != str(event.video_id)
            or payload["sheet_row"] != event.sheet_row):
        raise ValueError("snapshot_mismatch")
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def eligible(video, event):
    # S05 must lock this same Video row before changing state/generation.
    return (video.state == "READY" and video.generation == event.generation
            and not journal.is_deleted(video.id))


def attach_existing_ready():
    """Attach to the exact committed S04 event; never regenerate its ID/data."""
    ids = Outbox.objects.filter(kind="video.ready", ready_snapshot__isnull=True,
        job__video__state=Video.State.READY, job__generation=F("job__video__generation"))
    attached = 0
    for event_id in list(ids.order_by("due_at").values_list("pk", flat=True)[:100]):
        source = Outbox.objects.select_related("job").get(pk=event_id)
        with transaction.atomic():
            video = Video.objects.select_for_update().get(pk=source.job.video_id)
            if (video.state != Video.State.READY or video.generation != source.job.generation
                    or journal.is_deleted(video.id) or ReadyEvent.objects.filter(pk=event_id).exists()):
                continue
            SheetAllocator.objects.get_or_create(pk=1)
            allocator = SheetAllocator.objects.select_for_update().get(pk=1)
            entries, _ = journal.read_entries()
            reservations = [e for e in entries if e["kind"] == "sheet"]
            reservation = next((e for e in reservations if e["key"] == str(event_id)), None)
            if reservation:
                row = reservation["data"]["row"]
            else:
                row = max(allocator.next_row, max((e["data"]["row"] + 1 for e in reservations), default=2))
                journal.append("sheet", event_id, {"row": row,
                    "snapshot_sha256": hashlib.sha256(journal.canonical(source.payload)).hexdigest()})
            if reservation and reservation["data"]["snapshot_sha256"] != hashlib.sha256(journal.canonical(source.payload)).hexdigest():
                raise ValueError("restored_event_snapshot_conflict")
            allocator.next_row = max(allocator.next_row, row + 1)
            allocator.save(update_fields=["next_row"])
            event = ReadyEvent.objects.create(event_id=event_id, source=source, video=video,
                generation=source.job.generation, sheet_row=row, payload={**source.payload, "sheet_row": row})
            enqueue_existing(event.pk)
            attached += 1
    return attached


def enqueue_existing(event_id):
    """S06 attaches delivery metadata to an existing event; it does not create it."""
    event = ReadyEvent.objects.get(pk=event_id)
    encode_event(event)
    return Delivery.objects.get_or_create(event=event)[0]


def cancel_locked(video):
    """Call in S05's transaction AFTER locking Video and revoking READY/generation."""
    if not transaction.get_connection().in_atomic_block:
        raise RuntimeError("video_lock_required")
    Delivery.objects.filter(event__video=video, state__in=[
        Delivery.State.PENDING, Delivery.State.SENDING, Delivery.State.FAILED,
        Delivery.State.BLOCKED,
    ]).update(state=Delivery.State.CANCELLED, token=None, lease_until=None, reason="video_unavailable")


def claim(event_id, now=None):
    if settings.RESTORE_MODE:
        return None
    now = now or timezone.now()
    event = ReadyEvent.objects.get(pk=event_id)
    with transaction.atomic():
        video = Video.objects.select_for_update().get(pk=event.video_id)
        delivery = Delivery.objects.select_for_update().get(pk=event_id)
        if delivery.state == Delivery.State.SENDING:
            if delivery.lease_until is not None and delivery.lease_until > now:
                return None
        elif delivery.state != Delivery.State.PENDING or delivery.due_at > now:
            return None
        if not eligible(video, event):
            delivery.state, delivery.reason = Delivery.State.CANCELLED, "video_unavailable"
        elif delivery.attempts >= 5 or (delivery.started_at and now >= delivery.started_at + WINDOW):
            delivery.state, delivery.reason = Delivery.State.FAILED, "attempts_exhausted"
        else:
            encode_event(event)
            delivery.started_at = delivery.started_at or now
            delivery.attempts += 1
            delivery.total_attempts += 1
            delivery.state = Delivery.State.SENDING
            delivery.token = uuid.uuid4()
            delivery.lease_until = now + LEASE
            delivery.reason = ""
        delivery.save()
        return delivery if delivery.state == Delivery.State.SENDING else None


def finish(delivery, outcome, reason="", retry_after=0, now=None):
    now = now or timezone.now()
    with transaction.atomic():
        video = Video.objects.select_for_update().get(pk=delivery.event.video_id)
        current = Delivery.objects.select_for_update().get(pk=delivery.pk)
        if current.state != Delivery.State.SENDING or current.token != delivery.token:
            return  # a stale process cannot acknowledge a newer lease or deletion
        if not eligible(video, delivery.event):
            current.state, current.reason = Delivery.State.CANCELLED, "video_unavailable"
        elif outcome == "written":
            current.state, current.reason = Delivery.State.WRITTEN, ""
        elif outcome == "blocked":
            current.state, current.reason = Delivery.State.BLOCKED, reason
        else:
            due = max(current.started_at + timedelta(seconds=OFFSETS[min(current.attempts, 4)]),
                      now + timedelta(seconds=max(0, retry_after)))
            if current.attempts >= 5 or due >= current.started_at + WINDOW or now >= current.started_at + WINDOW:
                current.state, current.reason = Delivery.State.FAILED, "attempts_exhausted"
            else:
                current.state, current.reason, current.due_at = Delivery.State.PENDING, reason, due
        current.token, current.lease_until = None, None
        current.save()


def retry_manually(event_id, now=None):
    now = now or timezone.now()
    event = ReadyEvent.objects.get(pk=event_id)
    with transaction.atomic():
        video = Video.objects.select_for_update().get(pk=event.video_id)
        current = Delivery.objects.select_for_update().get(pk=event_id)
        if not eligible(video, event) or current.state not in (Delivery.State.FAILED, Delivery.State.BLOCKED):
            raise ValueError("delivery_not_retryable")
        current.state, current.attempts, current.started_at = Delivery.State.PENDING, 0, None
        current.due_at, current.reason = now, ""
        current.save()


def signature(secret, timestamp, body):
    return hmac.new(secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Webhook:
    def __init__(self, url, allowed_hosts, secret, *, allow_test_loopback=False):
        parts = urlsplit(url)
        local_test = allow_test_loopback and parts.hostname == "127.0.0.1" and parts.scheme == "http"
        if (not (parts.scheme == "https" or local_test) or not parts.hostname
                or parts.hostname not in allowed_hosts or parts.username or parts.password or parts.fragment):
            raise ValueError("webhook_destination_not_allowed")
        if len(secret) < 32:
            raise ValueError("webhook_secret_too_short")
        self.url, self.secret = url, secret
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), PinnedHTTPSHandler())

    def send(self, event, now=None):
        now = now or timezone.now()
        body = encode_event(event)
        timestamp = str(int(now.timestamp()))
        request = Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "User-Agent": "VidHarbor/1.0",
            "X-VidHarbor-Timestamp": timestamp,
            "X-VidHarbor-Signature": signature(self.secret, timestamp, body),
        })
        try:
            with self.opener.open(request, timeout=5) as response:
                raw = response.read(4097)
                if response.status != 200 or len(raw) > 4096:
                    return "retry", "invalid_ack", 0
                try:
                    ack = json.loads(raw)
                except (ValueError, UnicodeError):
                    return "retry", "invalid_ack", 0
                if ack != {"event_id": str(event.pk), "status": "written"}:
                    return "retry", "invalid_ack", 0
                return "written", "", 0
        except HTTPError as error:
            code = error.code
            delay = 0
            if code == 429 or 500 <= code <= 599:
                value = error.headers.get("Retry-After", "")
                try:
                    delay = max(0, int(value))
                except ValueError:
                    try:
                        delay = max(0, (parsedate_to_datetime(value).astimezone(dt_timezone.utc) - now).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
            error.close()
            return ("retry" if code == 429 or 500 <= code <= 599 else "blocked"), f"http_{code}", delay
        except (URLError, OSError, ValueError, HTTPException):
            return "retry", "transport_error", 0


def run_once(webhook, now=None):
    now = now or timezone.now()
    # Snapshot enumeration avoids holding locks across HTTP. claim rechecks everything.
    ids = Delivery.objects.filter(
        Q(state=Delivery.State.PENDING, due_at__lte=now)
        | Q(state=Delivery.State.SENDING, lease_until__lte=now)
    ).order_by("due_at").values_list("pk", flat=True)[:100]
    for event_id in list(ids):
        delivery = claim(event_id, now)
        if delivery is not None:
            outcome, reason, delay = webhook.send(delivery.event)
            finish(delivery, outcome, reason, delay)
            return True
    return False
