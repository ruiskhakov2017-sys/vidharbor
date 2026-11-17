"""Cached exporter in its own service; the disposable collector has a hard deadline."""
import os
import signal
import subprocess
import sys
import threading
import time
from wsgiref.simple_server import make_server

from prometheus_client.parser import text_string_to_metric_families

from .metrics import QuietHandler, authorized
from .telemetry import log_event

# Includes interpreter/Django startup under an enforced 1-CPU cgroup. Scrapes
# never wait for this budget: they read the already completed cached snapshot.
DEADLINE = 5.0
INTERVAL = 5.0
MAX_AGE = 15.0
MAX_BYTES = 128 * 1024


class SnapshotSampler:
    def __init__(self, command=None, *, timeout=DEADLINE):
        self.command = command or [sys.executable, 'manage.py', 'collect_metrics']
        self.timeout = timeout
        self.lock = threading.Lock()
        self.sample_lock = threading.Lock()
        self.data = b''
        self.completed = 0.0
        self.ok = False
        self.duration = 0.0
        self.failures = {'timeout': 0, 'collector_error': 0}
        self.stop = threading.Event()
        self.unreaped = None

    def sample(self):
        # Never create a second collector if an earlier call is still running.
        if self.stop.is_set() or not self.sample_lock.acquire(blocking=False):
            return
        started = time.monotonic()
        process = None
        reason = 'collector_error'
        data = b''
        success = False
        try:
            process = subprocess.Popen(self.command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, 'VIDHARBOR_OBSERVABILITY': '0'}, start_new_session=True)
            try:
                data, _ = process.communicate(timeout=self.timeout)
                success = process.returncode == 0 and 0 < len(data) <= MAX_BYTES
                if success:
                    families = list(text_string_to_metric_families(data.decode()))
                    success = any(family.name == 'vidharbor_backend_up' for family in families)
            except subprocess.TimeoutExpired:
                reason = 'timeout'
                os.killpg(process.pid, signal.SIGKILL)
                try:
                    process.communicate(timeout=.5)
                except subprocess.TimeoutExpired:
                    # A kernel-uninterruptible child must not accumulate new
                    # collectors or block the scrape server. Operator restart
                    # is required; expose collection_up=0 in the meantime.
                    self.unreaped = process
                    self.stop.set()
        except (OSError, ValueError, UnicodeError):
            success = False
        finally:
            with self.lock:
                self.duration = time.monotonic() - started
                self.ok = success
                if success:
                    self.data, self.completed = data, time.monotonic()
                else:
                    self.failures[reason] += 1
            self.sample_lock.release()
        if not success:
            log_event('metrics.collection_failed', error_code=reason)

    def ready(self):
        with self.lock:
            return self.ok and time.monotonic() - self.completed <= MAX_AGE

    def render(self):
        # No dependency I/O, subprocess wait, or collector execution in a scrape.
        with self.lock:
            age = max(0, time.monotonic() - self.completed) if self.completed else 0
            ready = self.ok and age <= MAX_AGE
            data = self.data if ready else b''
            duration = self.duration
            failures = dict(self.failures)
        health = (f'# TYPE vidharbor_metrics_collection_up gauge\nvidharbor_metrics_collection_up {int(ready)}\n'
            f'# TYPE vidharbor_metrics_snapshot_age_seconds gauge\nvidharbor_metrics_snapshot_age_seconds {age}\n'
            f'# TYPE vidharbor_metrics_collection_duration_seconds gauge\nvidharbor_metrics_collection_duration_seconds {duration}\n'
            '# TYPE vidharbor_metrics_collection_failures_total counter\n'
            + ''.join(f'vidharbor_metrics_collection_failures_total{{reason="{key}"}} {value}\n' for key,value in failures.items()))
        return health.encode() + data

    def run(self):
        while not self.stop.is_set():
            self.sample()
            self.stop.wait(INTERVAL)

    def app(self, environ, start_response):
        path = environ.get('PATH_INFO', '')
        if path == '/health/live':
            status, body = '200 OK', b'live'
        elif path == '/health/ready':
            status, body = ('200 OK', b'ready') if self.ready() else ('503 Service Unavailable', b'not ready')
        elif path == '/metrics':
            status, body = ('200 OK', self.render()) if authorized(environ.get('HTTP_AUTHORIZATION', '')) else ('401 Unauthorized', b'')
        else:
            status, body = '404 Not Found', b''
        start_response(status, [('Content-Type', 'text/plain; version=0.0.4'), ('Content-Length', str(len(body)))])
        return [body]


def serve(sampler=None):
    sampler = sampler or SnapshotSampler()
    threading.Thread(target=sampler.run, daemon=True).start()
    with make_server('0.0.0.0', 9100, sampler.app, handler_class=QuietHandler) as server:
        try:
            server.serve_forever()
        finally:
            sampler.stop.set()
