import os
import tempfile
import time
import threading
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pika
from django.contrib.auth import get_user_model
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.utils import timezone
from prometheus_client import CollectorRegistry, REGISTRY, generate_latest

from . import metrics, telemetry
from .models import Attempt, Job, Outbox, Upload, Video


class MetricsEndpointTests(SimpleTestCase):
    def test_metrics_are_opt_in_and_require_a_separate_secret(self):
        request=RequestFactory().get('/internal/metrics')
        with patch.object(telemetry,'enabled',return_value=False):self.assertEqual(metrics.http_metrics(request).status_code,404)
        with tempfile.TemporaryDirectory() as root:
            secret=Path(root)/'token';secret.write_text('x'*40)
            with patch.dict(os.environ,{'VIDHARBOR_METRICS_TOKEN_FILE':str(secret)}), patch.object(telemetry,'enabled',return_value=True), patch.object(telemetry,'service',return_value='upload'):
                self.assertEqual(metrics.http_metrics(request).status_code,401)
                request=RequestFactory().get('/internal/metrics',HTTP_AUTHORIZATION='Bearer '+'x'*40)
                response=metrics.http_metrics(request)
                self.assertEqual(response.status_code,200)
                self.assertNotIn(b'x'*40,response.content)

    def test_success_failure_and_duration_metrics_change(self):
        labels={'service':'metrics-unit','method':'POST','route':'api/uploads','status_class':'4xx'}
        with patch.object(telemetry,'enabled',return_value=True),patch.object(telemetry,'service',return_value='metrics-unit'):
            before=REGISTRY.get_sample_value('vidharbor_http_requests_total',labels) or 0
            metrics.observe_http('POST','api/uploads',400,.25)
            self.assertEqual(REGISTRY.get_sample_value('vidharbor_http_requests_total',labels),before+1)
            self.assertGreater(REGISTRY.get_sample_value('vidharbor_http_errors_total',{'service':'metrics-unit','status_class':'4xx'}),0)
            self.assertGreater(REGISTRY.get_sample_value('vidharbor_http_duration_seconds_sum',{k:v for k,v in labels.items() if k!='status_class'}),0)

    def test_component_liveness_is_distinct_from_ready_and_stale_heartbeat(self):
        def request(path):
            status=[]
            metrics.component_app({'PATH_INFO':path},lambda value,headers:status.append(value))
            return status[0]
        with patch.object(telemetry,'enabled',return_value=True),patch.object(telemetry,'service',return_value='metrics-unit'),patch.dict(metrics._state,{'ready':False,'tick':0}):
            self.assertEqual(request('/health/live'),'200 OK')
            self.assertEqual(request('/health/ready'),'503 Service Unavailable')
            metrics.tick(True)
            self.assertEqual(request('/health/ready'),'200 OK')
            with patch('vidharbor.metrics.time.monotonic',return_value=time.monotonic()+11):
                self.assertEqual(request('/health/ready'),'503 Service Unavailable')
            self.assertEqual(request('/metrics'),'401 Unauthorized')


class DurableMetricsTests(TestCase):
    def test_stalled_broker_preserves_postgres_metrics_and_reports_backend_down(self):
        release=threading.Event()
        def blocked():release.wait(5)
        registry=CollectorRegistry(auto_describe=False);registry.register(metrics.DatabaseCollector())
        try:
            with patch.object(metrics,'broker_observation',side_effect=blocked):
                started=time.monotonic();body=generate_latest(registry)
            self.assertLess(time.monotonic()-started,1.5)
            self.assertIn(b'vidharbor_backend_up{backend="postgres"} 1.0',body)
            self.assertIn(b'vidharbor_backend_up{backend="rabbitmq"} 0.0',body)
            self.assertIn(b'vidharbor_command_outbox_pending 0.0',body)
        finally:release.set()

    def test_retry_history_changes_the_retry_gauge(self):
        registry=CollectorRegistry(auto_describe=False);registry.register(metrics.DatabaseCollector())
        with patch('vidharbor.metrics.pika.BlockingConnection',side_effect=pika.exceptions.AMQPConnectionError):
            before=registry.get_sample_value('vidharbor_attempts',{'state':'retry'})
            owner=get_user_model().objects.create(username='retry-metric')
            upload=Upload.objects.create(owner=owner,title='Not a label',expected_size=1)
            video=Video.objects.create(owner=owner,upload=upload,title=upload.title)
            job=Job.objects.create(video=video,generation=1,state='RETRY_WAIT')
            Attempt.objects.create(job=job,number=1,state='RETRY')
            self.assertEqual(registry.get_sample_value('vidharbor_attempts',{'state':'retry'}),before+1)
            self.assertEqual(registry.get_sample_value('vidharbor_processing_jobs',{'state':'retry_wait'}),1)

    def test_business_metrics_use_committed_history_and_ignore_ready_in_relay_backlog(self):
        owner=get_user_model().objects.create(username='metrics-fixture')
        upload=Upload.objects.create(owner=owner,title='Never a label',expected_size=1)
        video=Video.objects.create(owner=owner,upload=upload,title=upload.title,state='READY',published_generation=1)
        job=Job.objects.create(video=video,generation=1,state='SUCCEEDED')
        Attempt.objects.create(job=job,number=1,state='SUCCEEDED',started_at=timezone.now()-timedelta(seconds=2),finished_at=timezone.now())
        Outbox.objects.create(job=job,kind='video.ready',attempt_no=0,payload={})
        registry=CollectorRegistry(auto_describe=False);registry.register(metrics.DatabaseCollector())
        with patch('vidharbor.metrics.pika.BlockingConnection',side_effect=pika.exceptions.AMQPConnectionError):
            self.assertEqual(registry.get_sample_value('vidharbor_processing_jobs',{'state':'ready'}),1)
            self.assertEqual(registry.get_sample_value('vidharbor_jobs_terminal_total',{'outcome':'success'}),1)
            self.assertEqual(registry.get_sample_value('vidharbor_command_outbox_pending'),0)
            self.assertEqual(registry.get_sample_value('vidharbor_attempt_duration_seconds_count'),1)
            self.assertEqual(registry.get_sample_value('vidharbor_backend_up',{'backend':'rabbitmq'}),0)
