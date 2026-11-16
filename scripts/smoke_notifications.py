"""Real signed loopback HTTP against existing S04 events; never n8n or Sheets."""
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError
from urllib.request import Request, urlopen

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vidharbor.settings")
import django
django.setup()

from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
from vidharbor.models import Delivery, Job, Outbox, ReadyEvent, ReceiverRow
from vidharbor.notifications import Webhook, attach_existing_ready, claim, finish, retry_manually
from vidharbor.test_receiver import receiver


def main():
    assert settings.DATABASES["default"]["NAME"] == "vidharbor_s04_test"
    assert not settings.RESTORE_MODE
    jobs_before = Job.objects.count()
    attach_existing_ready()
    events = list(ReadyEvent.objects.filter(delivery__state="PENDING").order_by("sheet_row"))
    assert len(events) >= 2, "Upload two real READY videos before this test"
    a, b = events[:2]
    source_snapshots = {e.pk: Outbox.objects.get(pk=e.pk).payload for e in (a,b)}
    secret = b"test-receiver-only-" + uuid.uuid4().hex.encode()
    server = receiver(secret, ("127.0.0.1", 0))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    webhook = Webhook(f"http://127.0.0.1:{server.server_port}/video-ready", ["127.0.0.1"], secret, allow_test_loopback=True)
    report = {"scope": "TEST HTTP receiver and PostgreSQL rows; real n8n/Sheets Not Run"}
    try:
        now = timezone.now(); sending = claim(a.pk, now); assert sending is not None
        original_open = webhook.opener.open
        def lose_reply(*args, **kwargs):
            with original_open(*args, **kwargs) as response:
                assert response.status == 200; response.read()
            raise URLError("injected lost response AFTER committed test row")
        with patch.object(webhook.opener, "open", side_effect=lose_reply):
            outcome, reason, delay = webhook.send(sending.event, now)
        assert outcome == "retry"
        finish(sending, outcome, reason, delay, now=now)
        assert ReceiverRow.objects.get(pk=a.sheet_row).event_id == a.pk
        sending = claim(a.pk, now + timedelta(seconds=60)); assert sending is not None
        outcome, reason, delay = webhook.send(sending.event, now + timedelta(seconds=60))
        finish(sending, outcome, reason, delay, now=now + timedelta(seconds=60))
        assert outcome == "written" and Delivery.objects.get(pk=a.pk).state == "WRITTEN"
        assert ReceiverRow.objects.filter(number=a.sheet_row).count() == 1
        report["lost_reply_retry"] = {"event_id":str(a.pk),"row":a.sheet_row,"same_snapshot":True}

        other_id = uuid.uuid4()
        rogue = SimpleNamespace(pk=other_id, video_id=b.video_id, sheet_row=b.sheet_row,
            payload={**b.payload, "event_id":str(other_id)})
        barrier = threading.Barrier(2)
        def deliver(event):
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                return webhook.send(event)
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            replies = list(pool.map(deliver, [b, rogue]))
        assert sorted(r[0] for r in replies) == ["blocked", "written"], replies
        assert ReceiverRow.objects.filter(number=b.sheet_row).count() == 1
        report["different_ids_same_empty_row"] = {"outcomes":replies,"row":b.sheet_row,"records":1}
        sending = claim(b.pk); assert sending is not None
        outcome, reason, delay = webhook.send(sending.event)
        finish(sending, outcome, reason, delay)
        if outcome == "blocked":
            # Operator resolves a conflict only in this disposable TEST receiver.
            ReceiverRow.objects.filter(number=b.sheet_row).delete()
            retry_manually(b.pk)
            sending = claim(b.pk)
            outcome, reason, delay = webhook.send(sending.event)
            finish(sending, outcome, reason, delay)
        assert outcome == "written" and ReceiverRow.objects.get(pk=b.sheet_row).event_id == b.pk
        for event in (a,b):
            assert Outbox.objects.get(pk=event.pk).payload == source_snapshots[event.pk]
            for _ in range(2): assert webhook.send(event)[0] == "written"
            assert ReceiverRow.objects.get(pk=event.sheet_row).payload == event.payload
            # Verify real HLS on the private test network. The external public
            # origin still needs the owner's separate n8n/network acceptance.
            path = event.payload["playlist_url"].split("/media/",1)[1]
            with urlopen(Request("http://web/media/"+path, headers={"Host":"127.0.0.1"}), timeout=10) as response:
                assert response.status == 200 and response.read().startswith(b"#EXTM3U")
        assert Job.objects.count() == jobs_before
        report.update(status="PASS", rows=[{"event_id":str(e.pk),"row":e.sheet_row} for e in (a,b)],
                      transcode_jobs_created_by_notifications=0, private_hls="PASS", external_public_origin="Not Run")
        print(json.dumps(report), flush=True)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


if __name__ == "__main__": main()
