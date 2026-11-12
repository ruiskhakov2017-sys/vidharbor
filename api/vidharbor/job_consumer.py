import fcntl
import json
import logging
import os
import threading
import time
import uuid

import pika
from django.conf import settings
from django.db import InterfaceError, OperationalError, close_old_connections, connection as database

from .job_attempts import MAX_ATTEMPTS, begin_attempt, finish_attempt
from .management.commands.relay_outbox import QUEUE, connect
from .models import Job


logger = logging.getLogger(__name__)
DATABASE_ERRORS = (OperationalError, InterfaceError)
RECONNECT_DELAY = 2
# With the broker's 20-delivery limit, contention can outlast the FFmpeg deadline.
LOCK_TIMEOUT = 15 * 60
LOCK_POLL_INTERVAL = 0.25


def parse_command(properties, body):
    payload = json.loads(body)
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise ValueError("unsupported command")
    for key in ("message_id", "job_id", "video_id"):
        if not isinstance(payload.get(key), str):
            raise ValueError("invalid identifier")
        uuid.UUID(payload[key])
    if payload["message_id"] != properties.message_id:
        raise ValueError("message ID mismatch")
    if type(payload.get("generation")) is not int or payload["generation"] < 1:
        raise ValueError("invalid generation")
    if type(payload.get("next_attempt_no")) is not int or not 1 <= payload["next_attempt_no"] <= MAX_ATTEMPTS:
        raise ValueError("invalid attempt number")
    return payload


def wait_for_database():
    paused = False
    while True:
        try:
            with database.cursor() as cursor:
                cursor.execute("SELECT 1")
        except DATABASE_ERRORS:
            if not paused:
                logger.warning("PostgreSQL unavailable; pausing consumption")
            paused = True
        else:
            if paused:
                logger.info("PostgreSQL available; resuming consumption")
            return
        finally:
            database.close()
        time.sleep(RECONNECT_DELAY)


def wait_for_lock(lock, cancelled, deadline, message_id):
    waiting = False
    while not cancelled.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            if not waiting:
                logger.info("Waiting for media lock for message %s", message_id)
                waiting = True
            cancelled.wait(min(LOCK_POLL_INTERVAL, remaining))
        else:
            return not cancelled.is_set()
    return False


def confirm_active_delivery(connection, channel, cancelled, database_unavailable, deadline):
    allowed = threading.Event()

    def authorize():
        if channel.is_open and not cancelled.is_set() and not database_unavailable.is_set():
            allowed.set()

    connection.add_callback_threadsafe(authorize)
    while not cancelled.is_set():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        if allowed.wait(min(LOCK_POLL_INTERVAL, remaining)):
            return not cancelled.is_set() and not database_unavailable.is_set()
    return False


def run_consumer(processor, *, heartbeat=30, lock_timeout=LOCK_TIMEOUT):
    """Consume using a supplied processor; callers must configure a safe DLX."""
    database_unavailable = threading.Event()
    while True:
        database_unavailable.clear()
        wait_for_database()
        connection = None
        cancelled = threading.Event()
        try:
            connection, channel = connect(
                heartbeat=heartbeat, connection_name=f"vidharbor-worker-{os.getpid()}"
            )
            channel.basic_qos(prefetch_count=1)

            def on_delivery(
                ch, method, properties, body, delivery_connection=connection, delivery_cancelled=cancelled
            ):
                if database_unavailable.is_set():
                    return
                try:
                    payload = parse_command(properties, body)
                except (ValueError, TypeError, KeyError, UnicodeError):
                    ch.basic_reject(method.delivery_tag, requeue=False)
                    return
                logger.info(
                    "Received job %s message %s redelivered=%s",
                    payload["job_id"], payload["message_id"], method.redelivered,
                )

                def complete():
                    if ch.is_open and not delivery_cancelled.is_set() and not database_unavailable.is_set():
                        ch.basic_ack(method.delivery_tag)

                def abandon():
                    delivery_cancelled.set()
                    if ch.is_open:
                        ch.close()

                def reject():
                    if ch.is_open and not delivery_cancelled.is_set() and not database_unavailable.is_set():
                        ch.basic_reject(method.delivery_tag, requeue=False)

                def process():
                    try:
                        close_old_connections()
                        job = Job.objects.filter(pk=payload["job_id"], video_id=payload["video_id"]).first()
                        if job is None:
                            logger.warning("Unknown job or video in message %s", payload["message_id"])
                            delivery_connection.add_callback_threadsafe(reject)
                            return
                        lock_path = settings.MEDIA_ROOT / "locks" / str(job.video_id)
                        with lock_path.open("ab") as lock:
                            deadline = time.monotonic() + lock_timeout
                            acquired = wait_for_lock(lock, delivery_cancelled, deadline, payload["message_id"])
                            if not acquired or not confirm_active_delivery(
                                delivery_connection, ch, delivery_cancelled, database_unavailable, deadline
                            ):
                                if delivery_cancelled.is_set():
                                    logger.info("Cancelled media lock wait for message %s", payload["message_id"])
                                elif not database_unavailable.is_set():
                                    logger.warning("Media lock wait expired for message %s", payload["message_id"])
                                    delivery_connection.add_callback_threadsafe(abandon)
                                return
                            attempt = begin_attempt(
                                job.id, payload["generation"], payload["next_attempt_no"], payload,
                                cancelled=delivery_cancelled,
                            )
                            if attempt is not None:
                                outcome, reason = processor(job, attempt, lock.fileno())
                                finish_attempt(attempt, outcome, payload, reason)
                        delivery_connection.add_callback_threadsafe(complete)
                    except DATABASE_ERRORS:
                        database_unavailable.set()
                        logger.warning("PostgreSQL connection lost; delivery remains unacknowledged", exc_info=True)
                    except pika.exceptions.ConnectionWrongStateError:
                        logger.info("Delivery connection closed for message %s", payload["message_id"])
                    except Exception:
                        logger.exception("Could not finish job; delivery remains unacknowledged")
                        try:
                            delivery_connection.add_callback_threadsafe(abandon)
                        except pika.exceptions.ConnectionWrongStateError:
                            pass
                    finally:
                        close_old_connections()

                threading.Thread(target=process, daemon=True).start()

            channel.add_on_cancel_callback(lambda _frame: cancelled.set())
            if not database_unavailable.is_set():
                channel.basic_consume(queue=QUEUE, on_message_callback=on_delivery, auto_ack=False)
            while channel.is_open and not cancelled.is_set() and not database_unavailable.is_set():
                connection.process_data_events(time_limit=1)
        except (pika.exceptions.AMQPError, OSError):
            logger.exception("Consumer connection failed")
        finally:
            cancelled.set()
            if connection is not None and connection.is_open:
                try:
                    connection.close()
                except (pika.exceptions.AMQPError, OSError):
                    logger.warning("Could not close consumer connection", exc_info=True)
        if not database_unavailable.is_set():
            time.sleep(RECONNECT_DELAY)
