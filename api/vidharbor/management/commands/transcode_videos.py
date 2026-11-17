import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from vidharbor.job_consumer import run_consumer
from vidharbor.publication import process_delivery
from vidharbor import metrics


class Command(BaseCommand):
    help = "Process queued videos and publish validated HLS."

    def handle(self, *args, **options):
        if settings.RESTORE_MODE:
            raise CommandError("Restore mode: worker must not consume before reconciliation")
        url = urlsplit(settings.PUBLIC_BASE_URL)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
            raise CommandError("PUBLIC_BASE_URL must be an HTTP(S) origin")
        logging.basicConfig(level=logging.INFO)
        logging.getLogger("vidharbor").setLevel(logging.INFO)
        metrics.start_component_server()
        run_consumer(delivery_handler=process_delivery)
