import io
import os
import sys
import tempfile
import threading
import time
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from django.core.management import call_command
from django.db import DatabaseError, connection
from django.test import RequestFactory, SimpleTestCase, TransactionTestCase

from . import metrics, telemetry
from .metrics_sampler import MAX_AGE, SnapshotSampler


class IsolatedMetricsTests(SimpleTestCase):
    def test_unreapable_child_stops_sampling_without_starting_more_children(self):
        child=Mock(pid=12345)
        child.communicate.side_effect=subprocess.TimeoutExpired('collector',2)
        sampler=SnapshotSampler()
        with patch('vidharbor.metrics_sampler.subprocess.Popen',return_value=child) as start, \
                patch('vidharbor.metrics_sampler.os.killpg'):
            sampler.sample();sampler.sample()
        self.assertEqual(start.call_count,1)
        self.assertIs(sampler.unreaped,child)
        self.assertTrue(sampler.stop.is_set())
        self.assertFalse(sampler.ready())

    def test_api_scrape_never_calls_a_database_or_broker_collector(self):
        with patch.object(telemetry, 'enabled', return_value=True), patch.object(telemetry, 'service', return_value='api'), \
                patch.object(metrics, 'authorized', return_value=True), \
                patch.object(metrics.DatabaseCollector, 'collect', side_effect=AssertionError('Dependency I/O in API')):
            self.assertEqual(metrics.http_metrics(RequestFactory().get('/internal/metrics')).status_code, 200)

    def test_hung_child_is_killed_while_cached_scrapes_remain_fast(self):
        with tempfile.TemporaryDirectory() as root:
            pidfile = Path(root)/'pid'
            code = f'import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(30)'
            sampler = SnapshotSampler([sys.executable, '-c', code], timeout=1)
            thread = threading.Thread(target=sampler.sample)
            started = time.monotonic(); thread.start()
            try:
                deadline = time.monotonic()+.8
                while not pidfile.exists() and time.monotonic()<deadline:
                    time.sleep(.005)
                self.assertTrue(pidfile.exists())
                pid = int(pidfile.read_text())
                for _ in range(8):
                    before = time.monotonic()
                    self.assertIn(b'vidharbor_metrics_collection_up 0', sampler.render())
                    self.assertLess(time.monotonic()-before, .1)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertLess(time.monotonic()-started, 2)
                self.assertEqual(sampler.failures['timeout'], 1)
                with self.assertRaises(ProcessLookupError):
                    os.kill(pid, 0)
            finally:
                thread.join(3)

    def test_stale_and_failed_snapshots_are_not_reported_as_current(self):
        sampler = SnapshotSampler([sys.executable, '-c', 'print(\'vidharbor_backend_up{backend="postgres"} 1\')'])
        sampler.sample()
        self.assertTrue(sampler.ready())
        self.assertIn(b'vidharbor_backend_up', sampler.render())
        with patch('vidharbor.metrics_sampler.time.monotonic', return_value=time.monotonic()+MAX_AGE+1):
            self.assertFalse(sampler.ready())
            self.assertNotIn(b'vidharbor_backend_up', sampler.render())
        sampler.command = [sys.executable, '-c', 'raise SystemExit(1)']
        sampler.sample()
        self.assertFalse(sampler.ready())
        self.assertNotIn(b'vidharbor_backend_up', sampler.render())

    def test_malformed_collector_output_does_not_become_a_successful_snapshot(self):
        sampler = SnapshotSampler([sys.executable, '-c', 'print("{not prometheus}")'])
        sampler.sample()
        self.assertFalse(sampler.ready())
        self.assertEqual(sampler.failures['collector_error'], 1)
        self.assertNotIn(b'{not prometheus}', sampler.render())

    def test_exporter_health_and_auth_do_not_wait_for_collection(self):
        sampler = SnapshotSampler()
        def request(path):
            result = []
            body = sampler.app({'PATH_INFO': path}, lambda status, headers: result.append(status))
            return result[0], b''.join(body)
        self.assertEqual(request('/health/live')[0], '200 OK')
        self.assertEqual(request('/health/ready')[0], '503 Service Unavailable')
        with patch('vidharbor.metrics_sampler.authorized', return_value=False):
            self.assertEqual(request('/metrics')[0], '401 Unauthorized')
        with patch('vidharbor.metrics_sampler.authorized', return_value=True):
            self.assertIn(b'vidharbor_metrics_collection_up 0', request('/metrics')[1])


class MetricsSQLBudgetTests(TransactionTestCase):
    def test_real_slow_sql_is_cancelled_by_collection_statement_budget(self):
        def slow(collector):
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_sleep(2)')
            yield from ()
        started = time.monotonic()
        with patch.object(metrics.DatabaseCollector, 'collect', slow):
            with self.assertRaises(DatabaseError):
                call_command('collect_metrics', stdout=io.StringIO())
        self.assertLess(time.monotonic()-started, 1.5)
