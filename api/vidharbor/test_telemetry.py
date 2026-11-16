import json
import logging
import os
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TransactionTestCase
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from . import telemetry
from .job_attempts import queue_retry
from .models import Attempt, Outbox, Upload
from .uploads import queue_video


class DiagnosticsTests(SimpleTestCase):
    def test_json_is_allowlisted_and_never_formats_exception_message_or_headers(self):
        secret = "private-cookie-and-authorization-value"
        record = logging.LogRecord("untrusted", logging.ERROR, "", 1, "body %s", (secret,), None)
        try:
            raise RuntimeError(secret)
        except RuntimeError as exc:
            record.exc_info = (type(exc), exc, exc.__traceback__)
        record.technical = {"job_id": uuid.uuid4(), "title": secret, "authorization": secret}
        result = telemetry.JsonFormatter().format(record)
        parsed = json.loads(result)
        self.assertNotIn(secret, result)
        self.assertEqual(parsed["error_code"], "RuntimeError")
        self.assertTrue({"timestamp", "level", "service", "event", "error_code", "trace_id", "correlation_id"} <= parsed.keys())
        self.assertEqual(parsed["job_id"], str(record.technical["job_id"]))

    def test_trace_carrier_strips_untrusted_fields_and_invalid_parent(self):
        parent = "00-" + "1"*32 + "-" + "2"*16 + "-01"
        self.assertEqual(telemetry.carrier({"traceparent": parent, "baggage":"private", "tracestate":"private"}), {"traceparent":parent})
        for value in (None, [], {"traceparent":0}, {"traceparent":"00-"+"0"*32+"-"+"2"*16+"-01"}):
            self.assertEqual(telemetry.carrier(value), {})

    def test_real_sdk_context_survives_serialization_between_operations(self):
        exporter = InMemorySpanExporter(); provider = TracerProvider(resource=Resource({"service.name":"test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self.addCleanup(provider.shutdown)
        with patch.dict(os.environ, {"VIDHARBOR_OBSERVABILITY":"1"}), patch.object(telemetry,"tracer",return_value=provider.get_tracer("test")):
            with telemetry.operation("HTTP"):
                with telemetry.operation("job.create"):
                    persisted = json.loads(json.dumps(telemetry.current_context()))
            with telemetry.operation("relay",parent=persisted):
                headers = telemetry.current_context()
            with telemetry.operation("worker",parent=headers):
                with telemetry.operation("ffmpeg"):
                    pass
        spans = {span.name:span for span in exporter.get_finished_spans()}
        self.assertEqual(len({span.context.trace_id for span in spans.values()}),1)
        for child,parent in (("job.create","HTTP"),("relay","job.create"),("worker","relay"),("ffmpeg","worker")):
            self.assertEqual(spans[child].parent.span_id,spans[parent].context.span_id)
        self.assertTrue(all(span.end_time > span.start_time for span in spans.values()))


class PersistedTraceTests(TransactionTestCase):
    def test_outbox_and_automatic_retry_keep_original_trace_carrier(self):
        exporter = InMemorySpanExporter(); provider = TracerProvider(resource=Resource({"service.name":"test"}))
        provider.add_span_processor(SimpleSpanProcessor(exporter)); self.addCleanup(provider.shutdown)
        owner = get_user_model().objects.create(username="tracing-test")
        upload = Upload.objects.create(owner=owner,title="Not logged",expected_size=1)
        with patch.dict(os.environ,{"VIDHARBOR_OBSERVABILITY":"1"}), patch.object(telemetry,"tracer",return_value=provider.get_tracer("test")):
            with telemetry.operation("HTTP"):
                job = queue_video(upload)
        message = Outbox.objects.get(job=job)
        self.assertTrue(telemetry.carrier(message.payload["trace_context"]))
        attempt = Attempt.objects.create(job=job,number=1,state="RETRY")
        queue_retry(job,attempt,message.payload)
        retried = Outbox.objects.get(job=job,attempt_no=2)
        self.assertEqual(retried.payload["trace_context"],message.payload["trace_context"])
        self.assertEqual(retried.payload["request_id"],str(upload.pk))
