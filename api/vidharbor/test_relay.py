import uuid
from unittest.mock import Mock

import pika
from django.contrib.auth import get_user_model
from django.test import TransactionTestCase
from django.utils import timezone

from .management.commands.relay_outbox import (
    ERROR_EXCHANGE,
    ERROR_ROUTING_KEY,
    ROUTING_KEY,
    publish_next,
)
from .models import Job, Outbox, Upload, Video


class RelayTests(TransactionTestCase):
    def setUp(self):
        author = get_user_model().objects.create_user(username="relay-author")
        upload = Upload.objects.create(owner=author, title="video", expected_size=1)
        video = Video.objects.create(upload=upload, owner=author, title="video")
        job = Job.objects.create(video=video, generation=1)
        message_id = uuid.uuid4()
        self.entry = Outbox.objects.create(
            message_id=message_id,
            job=job,
            kind=ROUTING_KEY,
            attempt_no=1,
            payload={"message_id": str(message_id), "job_id": str(job.id)},
        )

    def test_confirmed_publish_marks_entry_and_keeps_id(self):
        channel = Mock()
        self.assertTrue(publish_next(channel))
        self.entry.refresh_from_db()
        self.assertIsNotNone(self.entry.sent_at)
        arguments = channel.basic_publish.call_args.kwargs
        self.assertTrue(arguments["mandatory"])
        self.assertEqual(arguments["properties"].message_id, str(self.entry.message_id))
        self.assertEqual(arguments["properties"].delivery_mode, 2)
        self.assertFalse(publish_next(channel))
        channel.basic_publish.assert_called_once()

    def test_return_or_lost_confirmation_leaves_entry_pending(self):
        channel = Mock()
        for failure in (
            pika.exceptions.UnroutableError([]),
            pika.exceptions.AMQPConnectionError("confirmation lost"),
        ):
            channel.basic_publish.side_effect = failure
            with self.assertRaises(type(failure)):
                publish_next(channel)
            self.entry.refresh_from_db()
            self.assertIsNone(self.entry.sent_at)
        channel.basic_publish.side_effect = None
        self.assertTrue(publish_next(channel))
        self.entry.refresh_from_db()
        self.assertLessEqual(self.entry.sent_at, timezone.now())
        ids = [call.kwargs["properties"].message_id for call in channel.basic_publish.call_args_list]
        self.assertEqual(ids, [str(self.entry.message_id)] * 3)

    def test_terminal_event_uses_dead_letter_route(self):
        self.entry.delete()
        message_id = uuid.uuid4()
        event = Outbox.objects.create(
            message_id=message_id,
            job_id=self.entry.job_id,
            kind=ERROR_ROUTING_KEY,
            attempt_no=1,
            payload={"message_id": str(message_id), "job_id": str(self.entry.job_id)},
        )
        channel = Mock()
        channel.basic_publish.side_effect = pika.exceptions.UnroutableError([])
        with self.assertRaises(pika.exceptions.UnroutableError):
            publish_next(channel)
        event.refresh_from_db()
        self.assertIsNone(event.sent_at)
        channel.basic_publish.side_effect = None
        self.assertTrue(publish_next(channel))
        event.refresh_from_db()
        self.assertIsNotNone(event.sent_at)
        arguments = channel.basic_publish.call_args.kwargs
        self.assertEqual(arguments["exchange"], ERROR_EXCHANGE)
        self.assertEqual(arguments["routing_key"], ERROR_ROUTING_KEY)
        self.assertEqual(arguments["properties"].message_id, str(message_id))
