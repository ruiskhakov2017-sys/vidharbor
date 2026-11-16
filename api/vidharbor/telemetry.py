"""Allowlisted JSON diagnostics and bounded, optional OTLP tracing.

Business data and delivery decisions never depend on an exporter. No request
body, headers, query strings, exception messages or user data are serialized.
"""
import atexit
import functools
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from urllib.parse import urlsplit

_fields = ContextVar("vidharbor_diagnostics", default={})
_provider = None
_initialized = False
_initialization_lock = threading.Lock()
IDS = {"request_id", "correlation_id", "upload_id", "video_id", "job_id", "message_id", "event_id"}
NUMBERS = {"attempt", "generation", "status_code"}
TOKEN = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,79}\Z")
TRACEPARENT = re.compile(r"00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})\Z")


def enabled():
    return os.getenv("VIDHARBOR_OBSERVABILITY", "0") == "1"


def service():
    for command, name in (("relay_outbox", "relay"), ("transcode_videos", "worker"),
                          ("notify_ready", "notifier"), ("cleanup_uploads", "cleanup")):
        if command in sys.argv:
            return name
    value = os.getenv("VIDHARBOR_SERVICE_NAME", "api")
    return value if TOKEN.fullmatch(value) else "app"


def identifiers(values):
    result = {}
    for key, value in values.items():
        if key in IDS:
            try:
                result[key] = str(uuid.UUID(str(value)))
            except (ValueError, TypeError, AttributeError):
                pass
        elif key in NUMBERS and type(value) is int and value >= 0:
            result[key] = value
    return result


def identify(**values):
    _fields.set({**_fields.get(), **identifiers(values)})
    from opentelemetry import trace
    span = trace.get_current_span()
    if span.is_recording():
        for key, value in identifiers(values).items():
            span.set_attribute("vidharbor." + key, value)


@contextmanager
def bind(**values):
    token = _fields.set({**_fields.get(), **identifiers(values)})
    try:
        yield
    finally:
        _fields.reset(token)


def carrier(value):
    if not isinstance(value, dict):
        return {}
    parent = value.get("traceparent", "")
    match = TRACEPARENT.fullmatch(parent) if isinstance(parent, str) else None
    if match and int(match[1], 16) and int(match[2], 16):
        return {"traceparent": parent}
    return {}


def current_context():
    if not enabled():
        return {}
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    result = {}
    TraceContextTextMapPropagator().inject(result)
    return carrier(result)


def span_ids():
    from opentelemetry import trace
    context = trace.get_current_span().get_span_context()
    return ({"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}
            if context.is_valid else {"trace_id": None, "span_id": None})


class JsonFormatter(logging.Formatter):
    def format(self, record):
        event = getattr(record, "event", "diagnostic")
        code = getattr(record, "error_code", "")
        if record.exc_info:
            code = record.exc_info[0].__name__
        result = {"timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                  "level": record.levelname, "service": service(),
                  "event": event if isinstance(event, str) and TOKEN.fullmatch(event) else "diagnostic",
                  "error_code": code if isinstance(code, str) and (not code or TOKEN.fullmatch(code)) else "error",
                  **{key: None for key in sorted(IDS)}, **identifiers(_fields.get()), **span_ids()}
        result.update(identifiers(getattr(record, "technical", {})))
        # Deliberately do not call getMessage() or serialize args/exc_info.
        return json.dumps(result, ensure_ascii=True, separators=(",", ":"))


def log_event(event, *, error_code="", level=logging.INFO, **technical):
    logging.getLogger("vidharbor.events").log(level, event,
        extra={"event": event, "error_code": error_code, "technical": identifiers(technical)})
    if level >= logging.ERROR:
        from opentelemetry import trace
        from opentelemetry.trace import Status, StatusCode
        trace.get_current_span().set_status(Status(StatusCode.ERROR, error_code if TOKEN.fullmatch(error_code) else "error"))


def logging_config():
    handler = {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}
    return {"version": 1, "disable_existing_loggers": False,
            "formatters": {"json": {"()": "vidharbor.telemetry.JsonFormatter"}},
            "handlers": {"json": handler},
            "root": {"handlers": ["json"], "level": "WARNING"},
            "loggers": {name: {"handlers": ["json"], "level": level, "propagate": False}
                        for name, level in (("vidharbor", "INFO"), ("django", "WARNING"),
                                            ("gunicorn.error", "INFO"), ("gunicorn.access", "INFO"))}}


def tracer():
    global _provider, _initialized
    if not enabled():
        return None
    with _initialization_lock:
        if not _initialized:
            _initialized = True
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.resources import Resource
                from opentelemetry.sdk.trace import TracerProvider
                from opentelemetry.sdk.trace.export import BatchSpanProcessor
                import requests
                endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://tempo:4318/v1/traces")
                url = urlsplit(endpoint)
                if (url.scheme != "http" or url.hostname not in ("tempo", "localhost", "127.0.0.1")
                        or url.path != "/v1/traces" or url.query or url.fragment or url.username or url.password):
                    raise ValueError("internal_otlp_endpoint_required")
                session = requests.Session(); session.trust_env = False
                exporter = OTLPSpanExporter(endpoint=endpoint, timeout=1, session=session)
                from .metrics import ObservedExporter
                exporter = ObservedExporter(exporter)
                _provider = TracerProvider(resource=Resource({"service.name": service()}), shutdown_on_exit=False)
                _provider.add_span_processor(BatchSpanProcessor(exporter, max_queue_size=256,
                    max_export_batch_size=32, schedule_delay_millis=500, export_timeout_millis=1000))
                atexit.register(_provider.shutdown)
            except Exception:
                log_event("telemetry.unavailable", error_code="initialization_failed", level=logging.ERROR)
                _provider = None
    return _provider.get_tracer("vidharbor") if _provider else None


@contextmanager
def operation(name, *, parent=None, kind=None, **technical):
    with bind(**technical):
        active = tracer()
        if active is None:
            yield None
            return
        from opentelemetry.trace import SpanKind, Status, StatusCode
        from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
        context = TraceContextTextMapPropagator().extract(carrier(parent)) if parent is not None else None
        with active.start_as_current_span(name, context=context, kind=kind or SpanKind.INTERNAL,
                                         record_exception=False, set_status_on_exception=False) as span:
            identify(**_fields.get())
            try:
                yield span
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                log_event("operation.failed", error_code=type(exc).__name__, level=logging.ERROR)
                raise


def traced(name):
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            with operation(name) as span:
                result = function(*args, **kwargs)
                if span and isinstance(result, tuple) and len(result) == 2 and result[0] in ("success", "retry", "failed"):
                    from opentelemetry.trace import Status, StatusCode
                    if result[0] != "success":
                        span.set_status(Status(StatusCode.ERROR, result[1]))
                    log_event("processing.finished", error_code=result[1])
                return result
        return wrapped
    return decorate


class RequestTelemetry:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from opentelemetry.trace import SpanKind
        method = request.method if request.method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD") else "OTHER"
        request_id = str(uuid.uuid4())
        provided = identifiers({"correlation_id": request.headers.get("X-Correlation-ID")})
        with bind(request_id=request_id, correlation_id=provided.get("correlation_id", request_id)):
            if request.path.startswith(("/health/", "/internal/")):
                return self.get_response(request)
            with operation("HTTP " + method, parent={"traceparent": request.headers.get("traceparent", "")},
                           kind=SpanKind.SERVER) as span:
                started = time.monotonic()
                response = self.get_response(request)
                route = getattr(getattr(request, "resolver_match", None), "route", None) or "unmatched"
                if span:
                    span.update_name("HTTP " + method + " /" + route)
                    span.set_attribute("http.request.method", method)
                    span.set_attribute("http.route", route)
                    span.set_attribute("http.response.status_code", response.status_code)
                    if response.status_code >= 500:
                        from opentelemetry.trace import Status, StatusCode
                        span.set_status(Status(StatusCode.ERROR, f"http_{response.status_code}"))
                if enabled():
                    from .metrics import observe_http
                    observe_http(method,route,response.status_code,time.monotonic()-started)
                log_event("http.completed", error_code=f"http_{response.status_code}" if response.status_code >= 400 else "",
                          level=logging.ERROR if response.status_code >= 500 else logging.WARNING if response.status_code >= 400 else logging.INFO,
                          status_code=response.status_code)
                response["X-Correlation-ID"] = _fields.get()["correlation_id"]
                response["X-Request-ID"] = request_id
                current = current_context()
                if current:
                    response["traceparent"] = current["traceparent"]
                return response

    def process_view(self, request, view, args, kwargs):
        if "upload_id" in kwargs:
            identify(upload_id=kwargs["upload_id"], correlation_id=kwargs["upload_id"])
