"""Internal authenticated metrics. Durable business gauges come from PostgreSQL.

Only fixed state/route/service labels are used, never user IDs or video titles.
RabbitMQ depth means ready messages (passive declaration), not unacked messages.
"""
import hmac
import logging
import os
import threading
import time
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pika
from django.conf import settings
from django.db import DatabaseError, close_old_connections
from django.db.models import Count, DurationField, ExpressionWrapper, F, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from prometheus_client import Counter, Gauge, Histogram, REGISTRY, generate_latest
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

from . import telemetry

HTTP_REQUESTS = Counter("vidharbor_http_requests_total", "Completed business HTTP requests", ["service", "method", "route", "status_class"])
HTTP_ERRORS = Counter("vidharbor_http_errors_total", "HTTP 4xx and 5xx responses", ["service", "status_class"])
HTTP_DURATION = Histogram("vidharbor_http_duration_seconds", "Business HTTP request latency", ["service", "method", "route"],
                          buckets=(.01,.05,.1,.5,1,5,30,120,600))
EXPORTS = Counter("vidharbor_trace_exports_total", "OTLP export batches", ["service", "result"])
COMPONENT_READY = Gauge("vidharbor_component_ready", "Last component readiness observation", ["service"])
ACTIVE_DELIVERIES = Gauge("vidharbor_active_deliveries", "Currently executing delivery threads", ["service"])
_state = {"ready":False, "tick":0.0}
_server = None
_collector_registered = False
_lock = threading.Lock()


def observe_http(method, route, status, seconds):
    if not telemetry.enabled():return
    status_class = str(status//100)+"xx"
    HTTP_REQUESTS.labels(telemetry.service(),method,route,status_class).inc()
    HTTP_DURATION.labels(telemetry.service(),method,route).observe(seconds)
    if status >= 400:HTTP_ERRORS.labels(telemetry.service(),status_class).inc()


def tick(ready):
    if telemetry.enabled():
        _state.update(ready=bool(ready),tick=time.monotonic())
        COMPONENT_READY.labels(telemetry.service()).set(int(ready))


def active(delta):
    if telemetry.enabled():ACTIVE_DELIVERIES.labels(telemetry.service()).inc(delta)


def metrics_token():
    try:
        token = Path(os.getenv("VIDHARBOR_METRICS_TOKEN_FILE", "/run/secrets/telemetry_metrics_token")).read_bytes().strip()
        return token if len(token)>=32 else None
    except OSError:
        return None


def authorized(header):
    token = metrics_token()
    return token is not None and hmac.compare_digest(header.encode(), b"Bearer "+token)


class DatabaseCollector:
    def describe(self):
        return []  # Registration must never perform database or broker I/O.

    def collect(self):
        from .models import Attempt, Delivery, Job, Outbox, Video
        backend = GaugeMetricFamily("vidharbor_backend_up", "Dependency queried successfully during this scrape", labels=["backend"])
        try:
            current = dict(Job.objects.filter(generation=F("video__generation")).values_list("state").annotate(n=Count("pk")))
            jobs = GaugeMetricFamily("vidharbor_processing_jobs", "Current-generation jobs by state; ready means SUCCEEDED", labels=["state"])
            for db, label in (("QUEUED","queued"),("PROCESSING","processing"),("FAILED","failed"),
                              ("SUCCEEDED","ready"),("RETRY_WAIT","retry_wait"),("CANCELLED","cancelled")):
                jobs.add_metric([label],current.get(db,0))
            yield jobs
            videos = GaugeMetricFamily("vidharbor_videos", "Durable video records by publication state", labels=["state"])
            counts = dict(Video.objects.values_list("state").annotate(n=Count("pk")))
            for state in Video.State.values:videos.add_metric([state.lower()],counts.get(state,0))
            yield videos
            terminal = CounterMetricFamily("vidharbor_jobs_terminal", "Durable terminal job records; restores may reset totals", labels=["outcome"])
            counts = dict(Job.objects.values_list("state").annotate(n=Count("pk")))
            for state,label in (("SUCCEEDED","success"),("FAILED","failed")):
                terminal.add_metric([label],counts.get(state,0))
            yield terminal
            attempts = GaugeMetricFamily("vidharbor_attempts", "Persisted attempt history including retries", labels=["state"])
            counts = dict(Attempt.objects.values_list("state").annotate(n=Count("pk")))
            for state in Attempt.State.values:attempts.add_metric([state.lower()],counts.get(state,0))
            yield attempts
            finished = Attempt.objects.filter(finished_at__isnull=False).annotate(
                elapsed=ExpressionWrapper(F("finished_at")-F("started_at"),output_field=DurationField()))
            bounds=(.1,1,5,30,120,600,3600,10800)
            data=finished.aggregate(total=Count("pk"),seconds=Sum("elapsed"),**{
                "b"+str(i):Count("pk",filter=Q(elapsed__lte=timedelta(seconds=bound))) for i,bound in enumerate(bounds)})
            yield HistogramMetricFamily("vidharbor_attempt_duration_seconds", "Completed attempt durations from durable history",
                buckets=[(str(bound),data["b"+str(i)]) for i,bound in enumerate(bounds)]+[("+Inf",data["total"])],
                sum_value=data["seconds"].total_seconds() if data["seconds"] else 0)
            pending = Outbox.objects.filter(kind__in=("video.transcode.v1","video.failed.v1"),sent_at__isnull=True)
            yield GaugeMetricFamily("vidharbor_command_outbox_pending", "Unconfirmed relay commands (excludes video.ready)", value=pending.count())
            oldest=pending.order_by("due_at").values_list("due_at",flat=True).first()
            yield GaugeMetricFamily("vidharbor_command_outbox_oldest_age_seconds", "Age of oldest due unconfirmed command",
                value=max(0,(timezone.now()-oldest).total_seconds()) if oldest else 0)
            deliveries = GaugeMetricFamily("vidharbor_webhook_deliveries", "Delivery records by state, including test receiver", labels=["state"])
            counts = dict(Delivery.objects.values_list("state").annotate(n=Count("pk")))
            for state in Delivery.State.values:deliveries.add_metric([state.lower()],counts.get(state,0))
            yield deliveries
            yield CounterMetricFamily("vidharbor_webhook_attempts", "Durable total HTTP delivery attempts",
                value=Delivery.objects.aggregate(n=Sum("total_attempts"))["n"] or 0)
            backend.add_metric(["postgres"],1)
        except DatabaseError:
            backend.add_metric(["postgres"],0)
        try:
            params=pika.ConnectionParameters(host=settings.RABBITMQ_HOST,
                credentials=pika.PlainCredentials(settings.RABBITMQ_USER,settings.RABBITMQ_PASSWORD),
                heartbeat=10,connection_attempts=1,socket_timeout=1,stack_timeout=2,blocked_connection_timeout=1)
            with pika.BlockingConnection(params) as broker:
                channel=broker.channel()
                depth=GaugeMetricFamily("vidharbor_queue_ready_messages", "Ready messages only; excludes unacked", labels=["queue"])
                consumers=GaugeMetricFamily("vidharbor_queue_consumers", "Consumers observed by passive queue declaration", labels=["queue"])
                for queue in ("video.jobs","video.dlq"):
                    result=channel.queue_declare(queue=queue,passive=True)
                    depth.add_metric([queue],result.method.message_count)
                    consumers.add_metric([queue],result.method.consumer_count)
                yield depth;yield consumers
                backend.add_metric(["rabbitmq"],1)
        except (pika.exceptions.AMQPError,OSError):
            backend.add_metric(["rabbitmq"],0)
        yield backend


def render():
    global _collector_registered
    with _lock:
        if telemetry.service()=="api" and not _collector_registered:
            REGISTRY.register(DatabaseCollector());_collector_registered=True
    return generate_latest(REGISTRY)


def http_metrics(request):
    if not telemetry.enabled():return HttpResponse(status=404)
    if request.method!="GET":return HttpResponse(status=405)
    if not authorized(request.headers.get("Authorization","")):return HttpResponse(status=401)
    return HttpResponse(render(),content_type="text/plain; version=0.0.4; charset=utf-8")


class QuietHandler(WSGIRequestHandler):
    def log_message(self,*args):pass


def component_app(environ,start_response):
    path=environ.get("PATH_INFO","")
    if path=="/health/live":status,body="200 OK",b"live"
    elif path=="/health/ready":
        ready=_state["ready"] and time.monotonic()-_state["tick"]<10
        status,body=("200 OK",b"ready") if ready else ("503 Service Unavailable",b"not ready")
    elif path=="/metrics":
        if not authorized(environ.get("HTTP_AUTHORIZATION","")):status,body="401 Unauthorized",b""
        else:
            COMPONENT_READY.labels(telemetry.service()).set(int(_state["ready"] and time.monotonic()-_state["tick"]<10))
            status,body="200 OK",generate_latest(REGISTRY)
    else:status,body="404 Not Found",b""
    start_response(status,[("Content-Type","text/plain; version=0.0.4"),("Content-Length",str(len(body)))])
    return [body]


def start_component_server(port=9100):
    global _server
    if not telemetry.enabled():return None
    if _server is None:
        try:
            _server=make_server("0.0.0.0",port,component_app,handler_class=QuietHandler)
            threading.Thread(target=_server.serve_forever,daemon=True).start()
        except OSError:
            telemetry.log_event("metrics.unavailable",error_code="bind_failed",level=logging.ERROR)
    return _server


class ObservedExporter:
    def __init__(self, exporter):self.exporter=exporter
    def export(self, spans):
        from opentelemetry.sdk.trace.export import SpanExportResult
        try:
            result=self.exporter.export(spans)
        except Exception:
            EXPORTS.labels(telemetry.service(),"failed").inc()
            raise
        EXPORTS.labels(telemetry.service(),"success" if result==SpanExportResult.SUCCESS else "failed").inc()
        return result
    def shutdown(self):return self.exporter.shutdown()
    def force_flush(self,timeout_millis=1000):return self.exporter.force_flush(timeout_millis)
