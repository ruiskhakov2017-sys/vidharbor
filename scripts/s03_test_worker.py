"""Test-only consumer. Run only on an isolated Compose project."""
import os
import logging
import signal
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/app")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vidharbor.settings")

import django

django.setup()
from django.conf import settings

from vidharbor.job_consumer import run_consumer


logging.basicConfig(level=logging.INFO)
logging.getLogger("vidharbor.job_consumer").setLevel(logging.INFO)
if os.environ.get("VIDHARBOR_S03_TEST_ONLY") != "1":
    raise SystemExit("This consumer is limited to the S03 test stand")
if settings.DATABASES["default"]["NAME"] != "vidharbor_s03_test":
    raise SystemExit("This consumer requires the isolated S03 test database")
if os.environ.get("VIDHARBOR_S03_DB_PROXY_PORT"):
    settings.DATABASES["default"]["HOST"] = "127.0.0.1"
    settings.DATABASES["default"]["PORT"] = int(os.environ["VIDHARBOR_S03_DB_PROXY_PORT"])

scenario = os.environ.get("VIDHARBOR_S03_SCENARIO", "success")
if scenario not in {"success", "temporary", "invalid", "ffmpeg"}:
    raise SystemExit("Unknown S03 test scenario")
duration = int(os.environ.get("VIDHARBOR_S03_FFMPEG_DURATION", "18"))
if not 1 <= duration <= 1800:
    raise SystemExit("Invalid S03 test duration")


def process(job, attempt_id, lock_fd):
    if scenario == "temporary":
        return "retry", "temporary_test_failure"
    if scenario == "invalid":
        return "failed", "invalid_format"
    if scenario == "ffmpeg":
        directory = Path("/var/lib/vidharbor/s03-test") / str(job.video_id)
        directory.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "error", "-re",
                "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=5",
                "-t", str(duration), "-an", "-c:v", "mpeg4", "-f", "segment",
                "-segment_time", "1", str(directory / "part-%03d.ts"),
            ],
            pass_fds=(lock_fd,),
            start_new_session=True,
        )
        try:
            code = process.wait(timeout=3 * 60 * 60)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            return "failed", "ffmpeg_timeout"
        if code:
            return "failed", "ffmpeg_test_error"
    return "success", ""


run_consumer(process, heartbeat=int(os.environ.get("VIDHARBOR_S03_HEARTBEAT", "30")))
