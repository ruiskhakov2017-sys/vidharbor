import json
import logging
import time

import pika
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import DatabaseError, close_old_connections, transaction
from django.utils import timezone

from vidharbor.models import Outbox
from vidharbor import telemetry
from opentelemetry.trace import SpanKind


logger = logging.getLogger(__name__)
EXCHANGE = "video.commands"
ROUTING_KEY = "video.transcode.v1"
QUEUE = "video.jobs"
ERROR_EXCHANGE = "video.dead"
ERROR_QUEUE = "video.dlq"
ERROR_ROUTING_KEY = "video.failed.v1"
OUTBOX_KINDS = (ROUTING_KEY, ERROR_ROUTING_KEY)


def connect(*, heartbeat=30, connection_name=None):
    params = pika.ConnectionParameters(
        host=settings.RABBITMQ_HOST,
        credentials=pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD),
        heartbeat=heartbeat,
        connection_attempts=1,
        socket_timeout=3,
        stack_timeout=5,
        blocked_connection_timeout=5,
        client_properties={"connection_name": connection_name} if connection_name else None,
    )
    connection = pika.BlockingConnection(params)
    try:
        channel = connection.channel()
        channel.exchange_declare(exchange=EXCHANGE, exchange_type="direct", durable=True)
        channel.exchange_declare(exchange=ERROR_EXCHANGE, exchange_type="direct", durable=True)
        for queue in (QUEUE, ERROR_QUEUE):
            channel.queue_declare(queue=queue, durable=True, arguments={"x-queue-type": "quorum"})
        channel.queue_bind(queue=QUEUE, exchange=EXCHANGE, routing_key=ROUTING_KEY)
        channel.queue_bind(queue=ERROR_QUEUE, exchange=ERROR_EXCHANGE, routing_key=ERROR_ROUTING_KEY)
        channel.queue_bind(queue=ERROR_QUEUE, exchange=ERROR_EXCHANGE, routing_key="video.malformed.v1")
        channel.confirm_delivery()
    except Exception:
        connection.close()
        raise
    return connection, channel


def publish_next(channel):
    with transaction.atomic():
        entry = (
            Outbox.objects.select_for_update(skip_locked=True)
            .filter(kind__in=OUTBOX_KINDS, sent_at__isnull=True, due_at__lte=timezone.now())
            .order_by("due_at", "message_id")
            .first()
        )
        if entry is None:
            return False
        if entry.payload.get("message_id") != str(entry.message_id):
            raise ValueError(f"Outbox message ID mismatch: {entry.message_id}")
        parent = entry.payload.get("trace_context", {})
        if telemetry.enabled() and not parent:
            original = Outbox.objects.filter(job_id=entry.job_id, kind=ROUTING_KEY).order_by("attempt_no").first()
            parent = original.payload.get("trace_context", {}) if original else {}
        with telemetry.operation("outbox.publish", parent=parent, kind=SpanKind.PRODUCER,
                job_id=entry.job_id, message_id=entry.pk, video_id=entry.job.video_id,
                correlation_id=entry.job.video.upload_id, upload_id=entry.job.video.upload_id):
            headers = telemetry.current_context()
            if headers:
                headers["x-vidharbor-correlation-id"] = str(entry.job.video.upload_id)
            channel.basic_publish(
                exchange=EXCHANGE if entry.kind == ROUTING_KEY else ERROR_EXCHANGE,
                routing_key=entry.kind,
                body=json.dumps(entry.payload, separators=(",", ":")).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json", delivery_mode=2,
                    message_id=str(entry.message_id), headers=headers or None,
                ),
                mandatory=True,
            )
            entry.sent_at = timezone.now()
            entry.save(update_fields=["sent_at"])
            telemetry.log_event("outbox.published")
    logger.info("Published outbox message %s", entry.message_id)
    return True


class Command(BaseCommand):
    help = "Publish due video commands from PostgreSQL to RabbitMQ."

    def handle(self, *args, **options):
        from django.conf import settings
        from django.core.management.base import CommandError
        if settings.RESTORE_MODE:
            raise CommandError("Restore mode: relay is paused")
        connection = None
        while True:
            try:
                close_old_connections()
                if connection is None or connection.is_closed:
                    connection, channel = connect()
                if not publish_next(channel):
                    connection.process_data_events(time_limit=1)
            except (pika.exceptions.AMQPError, OSError, DatabaseError, ValueError):
                logger.exception("Outbox relay failed; the message remains available for retry")
                if connection is not None and connection.is_open:
                    try:
                        connection.close()
                    except (pika.exceptions.AMQPError, OSError):
                        logger.warning("Could not close RabbitMQ connection", exc_info=True)
                connection = None
                close_old_connections()
                time.sleep(2)
