"""Real process/flock regression checks. Run in the upload image, never on production."""

import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from vidharbor.transcode import stop_group


WRITER = '''
import os, signal, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).write_text(str(os.getpid()))
with open(sys.argv[2], 'ab', buffering=0) as output:
    while True:
        output.write(b'x' * 4096)
        time.sleep(.02)
'''
LAUNCHER = '''
import fcntl, json, subprocess, sys, time
from pathlib import Path
root = Path(sys.argv[1])
with (root/'lock').open('ab') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    monitor = subprocess.Popen(
        ['timeout', '-k', '5', sys.argv[2], sys.executable, '-c', sys.argv[3],
         str(root/'writer'), str(root/'output')],
        start_new_session=True, pass_fds=(lock.fileno(),),
    )
    (root/'monitor').write_text(json.dumps({'pid': monitor.pid}))
    while True:
        time.sleep(1)
'''


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='s04-supervisor-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.worker = None
        self.group = None
        self.monitor = None
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        if self.group is not None:
            try:
                os.killpg(self.group, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if self.monitor is not None:
            self.monitor.wait(timeout=5)
        if self.worker is not None:
            if self.worker.poll() is None:
                self.worker.kill()
            self.worker.wait(timeout=5)

    def wait_for(self, predicate, seconds=5):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.02)
        self.fail('Process checkpoint did not arrive')

    def can_lock(self):
        with (self.root/'lock').open('ab') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            return True

    def start_orphan(self, timeout='10800'):
        self.worker = subprocess.Popen([sys.executable, '-c', LAUNCHER, str(self.root), timeout, WRITER])
        self.wait_for(lambda: all((self.root/name).exists() for name in ('monitor', 'writer', 'output')))
        self.group = json.loads((self.root/'monitor').read_text())['pid']
        self.writer = int((self.root/'writer').read_text())
        self.inode = (self.root/'lock').stat().st_ino
        self.assertEqual(os.getpgid(self.writer), self.group)
        self.assertEqual(os.getsid(self.writer), self.group)
        self.worker.kill()
        self.worker.wait(timeout=5)
        before = (self.root/'output').stat().st_size
        self.wait_for(lambda: (self.root/'output').stat().st_size > before)
        self.assertFalse(self.can_lock(), 'Live orphan writer must retain flock')

    def assert_released(self):
        self.wait_for(self.can_lock, seconds=8)
        self.assertEqual((self.root/'lock').stat().st_ino, self.inode)
        size = (self.root/'output').stat().st_size
        time.sleep(.15)
        self.assertEqual((self.root/'output').stat().st_size, size)

    def test_worker_death_then_group_kill_releases_same_lock(self):
        self.start_orphan()
        os.killpg(self.group, signal.SIGKILL)
        self.assert_released()

    def test_dead_monitor_does_not_unlock_live_writer(self):
        with (self.root/'lock').open('ab') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.monitor = subprocess.Popen(
                ['timeout', '-k', '5', '10800', sys.executable, '-c', WRITER,
                 str(self.root/'writer'), str(self.root/'output')],
                start_new_session=True, pass_fds=(lock.fileno(),),
            )
        self.group = self.monitor.pid
        self.wait_for(lambda: (self.root/'writer').exists() and (self.root/'output').exists())
        self.inode = (self.root/'lock').stat().st_ino
        self.monitor.kill()
        self.monitor.wait(timeout=5)
        before = (self.root/'output').stat().st_size
        self.wait_for(lambda: (self.root/'output').stat().st_size > before)
        self.assertFalse(self.can_lock(), 'Writer must keep lock after monitor dies')
        stop_group(self.monitor)
        self.assert_released()

    def test_dead_worker_keeps_independent_deadline(self):
        self.start_orphan(timeout='1')
        # The writer ignores TERM: the real GNU monitor must use the unchanged -k 5.
        self.assert_released()


if __name__ == '__main__':
    version = subprocess.run(['timeout', '--version'], capture_output=True, text=True)
    if version.returncode or 'GNU coreutils' not in version.stdout:
        raise SystemExit('FAIL: upload image must provide GNU coreutils timeout')
    print(version.stdout.splitlines()[0], flush=True)
    unittest.main(verbosity=2)
