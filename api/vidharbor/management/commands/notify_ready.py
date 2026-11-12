import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from vidharbor.models import ReadyEvent
from vidharbor.notifications import Webhook, enqueue_existing, retry_manually, run_once


class Command(BaseCommand):
    help = "Deliver existing S04 video.ready events; no publication or transcoding."

    def add_arguments(self, parser):
        parser.add_argument("--url")
        parser.add_argument("--allow-host", action="append", default=[])
        parser.add_argument("--secret-file", default="/run/secrets/webhook_secret")
        parser.add_argument("--allow-test-loopback", action="store_true")
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--retry", help="Manually retry a blocked/exhausted event UUID.")

    def handle(self, *args, **options):
        if options["retry"]:
            try:
                retry_manually(options["retry"])
            except (ValueError, ReadyEvent.DoesNotExist):
                raise CommandError("Event is missing or not retryable") from None
            self.stdout.write("Delivery scheduled with the existing event ID and row.")
            return
        try:
            secret = Path(options["secret_file"]).read_bytes().strip()
            webhook = Webhook(options["url"] or "", options["allow_host"], secret,
                              allow_test_loopback=options["allow_test_loopback"])
        except (OSError, ValueError):
            raise CommandError("Check webhook destination allowlist and secret file (at least 32 bytes).") from None
        while True:
            close_old_connections()
            for event_id in ReadyEvent.objects.filter(delivery__isnull=True).values_list("pk", flat=True)[:100]:
                enqueue_existing(event_id)
            worked = run_once(webhook)
            if options["once"]:
                return
            if not worked:
                time.sleep(1)

