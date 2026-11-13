"""Exercise S04 faults on the isolated Compose stand after stopping worker and relay."""

import fcntl
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vidharbor.settings")

import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone

from vidharbor import job_consumer, publication, transcode
from vidharbor.management.commands import relay_outbox as relay
from vidharbor.models import Job, Outbox, Upload, Video
from vidharbor.storage import file_digest, original_path


if settings.DATABASES["default"]["NAME"] != "vidharbor_s04_test":
    raise SystemExit("This test requires the isolated S04 database")

FIXTURE = Path("/fixtures/landscape.mp4")


def make_job(channel, *, corrupt=False):
    author = get_user_model().objects.create_user(username="fault-" + uuid.uuid4().hex[:10])
    upload = Upload.objects.create(
        owner=author, title="S04 faults", expected_size=FIXTURE.stat().st_size, state=Upload.State.COMPLETE,
    )
    original = original_path(upload.id)
    if corrupt:
        original.write_bytes(b"truncated video input")
    else:
        shutil.copyfile(FIXTURE, original)
    video = Video.objects.create(upload=upload, owner=author, title=upload.title)
    job = Job.objects.create(video=video, generation=1)
    message = uuid.uuid4()
    payload = {
        "version": 1, "job_id": str(job.id), "video_id": str(video.id), "generation": 1,
        "next_attempt_no": 1, "message_id": str(message), "request_id": str(upload.id), "trace_context": {},
    }
    event = Outbox.objects.create(message_id=message, job=job, kind=relay.ROUTING_KEY, attempt_no=1, payload=payload)
    assert relay.publish_next(channel)
    event.refresh_from_db()
    assert event.sent_at
    return job, event


def http_status(path):
    try:
        with urlopen(Request("http://web" + path, headers={"Host": "127.0.0.1"}), timeout=15) as response:
            return response.status
    except HTTPError as error:
        return error.code


def assert_hidden(job):
    video_id = job.video_id
    for path in (
        f"/watch/{video_id}", f"/api/catalog/{video_id}", f"/media/{video_id}/master.m3u8",
        f"/media/{video_id}/g1/v0/index.m3u8", f"/media/{video_id}/g1/v0/seg-000000.ts",
    ):
        assert http_status(path) == 404, path
    assert http_status(f"/_vidharbor_hls/{video_id}/g1/master.m3u8") == 404


def read_log(path):
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def wait_for(amqp, check, *, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        amqp.process_data_events(time_limit=.3)
    raise AssertionError("Timed out waiting for fault-test checkpoint")


def job_state(job, target):
    job.refresh_from_db()
    return job.state == target


def assert_ready(job, *, attempts):
    job.refresh_from_db()
    video = Video.objects.get(pk=job.video_id)
    assert job.state == Job.State.SUCCEEDED and video.state == Video.State.READY
    assert video.published_generation == job.generation and video.ready_at
    history = list(job.attempts.order_by("number").values_list("state", "reason"))
    assert history == attempts, history
    event = Outbox.objects.get(kind=publication.READY_EVENT, job=job)
    payload = event.payload
    assert payload == {
        "type": "video.ready", "version": 1, "event_id": str(event.message_id), "video_id": str(video.id),
        "title": video.title, "ready_at": video.ready_at.isoformat().replace("+00:00", "Z"),
        "playlist_url": settings.PUBLIC_BASE_URL + publication.master_url(video.id),
    }, payload
    assert event.attempt_no == 0 and event.sent_at is None
    assert http_status(f"/watch/{video.id}") == 200
    assert http_status(publication.master_url(video.id)) == 200
    directory = publication.ready_path(video.id, job.generation)
    attempt = job.attempts.get(state="SUCCEEDED")
    publication.verify_manifest(directory, job, attempt.pk)
    print(f"READY job={job.id} event={event.message_id} attempts={history} playlist_url={payload['playlist_url']}", flush=True)
    return event


def workers_for(scratch):
    processes = []

    def start(mode, job=None):
        marker = scratch / f"{mode}.marker"
        logfile = scratch / f"{mode}.log"
        with logfile.open("w") as log:
            process = subprocess.Popen(
                [sys.executable, "-u", __file__, "--worker"],
                env={**os.environ, "S04_TEST_MODE": mode, "S04_MARKER": str(marker),
                     "S04_TEST_VIDEO": str(job.video_id) if job else ""},
                stdout=log, stderr=log,
            )
        processes.append((process, logfile))
        return process, marker, logfile

    return processes, start


def stop(worker, *, kill=False):
    if worker.poll() is None:
        if kill:
            worker.kill()
        else:
            worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()


def retry_delivery(amqp, channel, job, reason):
    wait_for(amqp, lambda: job_state(job, Job.State.RETRY_WAIT))
    attempt = job.attempts.get(number=1)
    assert (attempt.state, attempt.reason) == ("RETRY", reason), (attempt.state, attempt.reason)
    event = Outbox.objects.get(job=job, kind=relay.ROUTING_KEY, attempt_no=2)
    wait_for(amqp, lambda: event.due_at <= timezone.now())
    assert relay.publish_next(channel)
    event.refresh_from_db()
    assert event.sent_at
    wait_for(amqp, lambda: job_state(job, Job.State.SUCCEEDED))


def worker_mode():
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("vidharbor").setLevel(logging.INFO)
    mode = os.environ["S04_TEST_MODE"]
    marker = Path(os.environ["S04_MARKER"])

    if mode in ("rename", "corrupt", "database"):
        original_create = Outbox.objects.create

        def create(**values):
            if values.get("kind") == publication.READY_EVENT and not marker.exists():
                if mode == "database":
                    marker.write_text(str(connection.connection.info.backend_pid), encoding="utf-8")
                    deadline = time.monotonic() + 45
                    while not marker.with_suffix(".resume").exists():
                        if time.monotonic() >= deadline:
                            raise RuntimeError("Missing DB fault from controller")
                        time.sleep(.15)
                else:
                    marker.touch()
                    os.kill(os.getpid(), signal.SIGSTOP)
            return original_create(**values)

        Outbox.objects.create = create

    if mode in ("ack", "hold-ack", "close-channel"):
        original_connect = job_consumer.connect

        def connect(**options):
            conn, channel = original_connect(**options)
            if mode in ("ack", "hold-ack"):
                acknowledge = channel.basic_ack

                def ack(*args, **kwargs):
                    if not marker.exists():
                        marker.touch()
                        if mode == "ack":
                            os.kill(os.getpid(), signal.SIGSTOP)
                        return
                    return acknowledge(*args, **kwargs)

                channel.basic_ack = ack
            else:
                def close_after_output():
                    if marker.exists():
                        return
                    directory = publication.staging_path(os.environ["S04_TEST_VIDEO"])
                    if any(directory.glob("v*/seg-*.ts")):
                        marker.touch()
                        channel.queue_declare(queue="s04-missing-" + uuid.uuid4().hex, passive=True)
                    else:
                        conn.call_later(.3, close_after_output)

                conn.call_later(.3, close_after_output)
            return conn, channel

        job_consumer.connect = connect

    if mode in ("orphan", "close-channel", "ffmpeg-timeout"):
        if mode == "ffmpeg-timeout":
            settings.FFMPEG_TIMEOUT = 2
        original_command = transcode.command_for
        popen = subprocess.Popen

        def command(*args, **kwargs):
            result = original_command(*args, **kwargs)
            if mode == "orphan" or not marker.exists():
                index = result.index("-i")
                result[index:index] = ["-re", "-stream_loop", "3"]
            return result

        def start(command, *args, **kwargs):
            process = popen(command, *args, **kwargs)
            if command[0] == "timeout" and mode == "orphan":
                marker.write_text(str(process.pid), encoding="utf-8")
            return process

        transcode.command_for = command
        subprocess.Popen = start

    job_consumer.run_consumer(delivery_handler=publication.process_delivery)


def main():
    import argparse
    import tempfile

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ack-timeout", action="store_true", help="Also check the broker's temporary 60s ack timeout.")
    options = parser.parse_args()
    amqp, channel = relay.connect()
    assert channel.queue_declare(queue=relay.QUEUE, passive=True).method.consumer_count == 0
    with tempfile.TemporaryDirectory(prefix="s04-faults-") as work:
        scratch = Path(work)
        processes, start = workers_for(scratch)
        group = None
        try:
            job, message = make_job(channel)
            print(f"Crash before commit and after commit; job={job.id} command={message.message_id}", flush=True)
            original_hash = file_digest(original_path(job.video.upload_id))
            first, marker, _ = start("rename")
            wait_for(amqp, marker.exists)
            assert_hidden(job)
            directory = publication.ready_path(job.video_id, 1)
            manifest_hash = file_digest(directory / publication.MANIFEST)
            assert not Outbox.objects.filter(job=job, kind=publication.READY_EVENT).exists()
            stop(first, kill=True)
            second, marker, logfile = start("ack")
            wait_for(amqp, marker.exists)
            assert "Recovered publication" in read_log(logfile)
            event = assert_ready(job, attempts=[("SUCCEEDED", "")])
            assert file_digest(directory / publication.MANIFEST) == manifest_hash
            assert file_digest(original_path(job.video.upload_id)) == original_hash
            stop(second, kill=True)
            third, _, logfile = start("duplicate")
            wait_for(amqp, lambda: f"message {message.message_id} redelivered=True" in read_log(logfile))
            amqp.process_data_events(time_limit=2)
            stop(third)
            assert channel.basic_get(relay.QUEUE)[0] is None
            assert assert_ready(job, attempts=[("SUCCEEDED", "")]).message_id == event.message_id
            stale = publication.ready_path(job.video_id, 99)
            shutil.copytree(directory, stale)
            assert http_status(f"/media/{job.video_id}/g99/v0/index.m3u8") == 404
            shutil.rmtree(stale)
            print("Rename before commit: 404; recovered same manifest; post-commit duplicate acked; event unchanged.", flush=True)

            job, message = make_job(channel)
            first, marker, _ = start("corrupt")
            wait_for(amqp, marker.exists)
            directory = publication.ready_path(job.video_id, 1)
            next(directory.glob("v*/seg-*.ts")).unlink()
            assert_hidden(job)
            stop(first, kill=True)
            second, _, logfile = start("repair")
            retry_delivery(amqp, channel, job, "worker_lost")
            assert "Discarding unpublished invalid HLS" in read_log(logfile)
            assert_ready(job, attempts=[("RETRY", "worker_lost"), ("SUCCEEDED", "")])
            stop(second)
            print(f"Missing segment: remained 404, rejected manifest, re-encoded; command={message.message_id}", flush=True)

            job, message = make_job(channel)
            worker, marker, logfile = start("database")
            wait_for(amqp, marker.exists)
            backend_pid = int(marker.read_text())
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s)", [backend_pid])
                assert cursor.fetchone() == (True,)
            assert_hidden(job)
            marker.with_suffix(".resume").touch()
            wait_for(amqp, lambda: job_state(job, Job.State.SUCCEEDED))
            assert worker.poll() is None
            assert "PostgreSQL connection lost" in read_log(logfile)
            assert f"message {message.message_id} redelivered=True" in read_log(logfile)
            assert_ready(job, attempts=[("SUCCEEDED", "")])
            stop(worker)
            print(f"PostgreSQL connection terminated at publication: same worker PID={worker.pid} recovered.", flush=True)

            job, message = make_job(channel)
            old, marker, _ = start("orphan")
            directory = publication.staging_path(job.video_id)
            wait_for(amqp, lambda: marker.exists() and any(directory.glob("v*/seg-*.ts")))
            group = int(marker.read_text())
            stop(old, kill=True)
            new, _, logfile = start("lock-wait")
            wait_for(amqp, lambda: "Waiting for media lock" in read_log(logfile))
            with (settings.MEDIA_ROOT / "locks" / str(job.video_id)).open("ab") as held:
                try:
                    fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    raise AssertionError("The surviving FFmpeg did not retain the lock")
            before = sum(file.stat().st_size for file in directory.glob("v*/seg-*.ts"))
            wait_for(amqp, lambda: sum(file.stat().st_size for file in directory.glob("v*/seg-*.ts")) > before)
            after = sum(file.stat().st_size for file in directory.glob("v*/seg-*.ts"))
            assert job.attempts.count() == 1
            assert_hidden(job)
            started = time.monotonic()
            assert http_status("/api/catalog") == 200
            print(f"Catalog answered 200 during FFmpeg in {time.monotonic()-started:.3f}s", flush=True)
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            group = None
            retry_delivery(amqp, channel, job, "worker_lost")
            assert_ready(job, attempts=[("RETRY", "worker_lost"), ("SUCCEEDED", "")])
            stop(new)
            print(f"Surviving FFmpeg held flock and wrote {before} -> {after} bytes; next worker waited; command={message.message_id}", flush=True)

            job, message = make_job(channel)
            worker, marker, logfile = start("close-channel", job)
            wait_for(amqp, marker.exists)
            retry_delivery(amqp, channel, job, "channel_lost")
            assert_ready(job, attempts=[("RETRY", "channel_lost"), ("SUCCEEDED", "")])
            stop(worker)
            print(f"Broker channel loss stopped the old encode; resumed with a new attempt; command={message.message_id}", flush=True)

            job, message = make_job(channel)
            worker, _, _ = start("ffmpeg-timeout")
            wait_for(amqp, lambda: job_state(job, Job.State.FAILED))
            assert job.attempts.get().reason == "ffmpeg_timeout"
            assert_hidden(job)
            assert not Outbox.objects.filter(job=job, kind=publication.READY_EVENT).exists()
            assert relay.publish_next(channel)
            stop(worker)
            print(f"FFmpeg deadline: one failed attempt, no publication; command={message.message_id}", flush=True)

            job, message = make_job(channel, corrupt=True)
            worker, _, _ = start("invalid-source")
            wait_for(amqp, lambda: job_state(job, Job.State.FAILED))
            assert job.attempts.get().reason == "invalid_video"
            assert not Outbox.objects.filter(job=job, kind=publication.READY_EVENT).exists()
            assert_hidden(job)
            assert relay.publish_next(channel)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                method, properties, body = channel.basic_get(relay.ERROR_QUEUE, auto_ack=True)
                if method and json.loads(body)["job_id"] == str(job.id):
                    print(f"Invalid source: one failed attempt, 404, DLQ message={properties.message_id}", flush=True)
                    break
                amqp.process_data_events(time_limit=.3)
            else:
                raise AssertionError("No terminal event in RabbitMQ DLQ")
            stop(worker)

            if options.ack_timeout:
                job, message = make_job(channel)
                worker, marker, logfile = start("hold-ack")
                wait_for(amqp, marker.exists)
                event = assert_ready(job, attempts=[("SUCCEEDED", "")])
                started = time.monotonic()
                wait_for(amqp, lambda: f"message {message.message_id} redelivered=True" in read_log(logfile), timeout=180)
                amqp.process_data_events(time_limit=2)
                stop(worker)
                assert channel.basic_get(relay.QUEUE)[0] is None
                assert assert_ready(job, attempts=[("SUCCEEDED", "")]).message_id == event.message_id
                print(f"Ack timeout: {time.monotonic()-started:.1f}s to redelivery; unchanged event; command={message.message_id}", flush=True)
        except Exception:
            for _, logfile in processes:
                print(f"--- {logfile.name} ---\n{read_log(logfile)[-5000:]}", flush=True)
            raise
        finally:
            if group is not None:
                try:
                    os.killpg(group, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            for worker, _ in processes:
                stop(worker, kill=True)
            amqp.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        worker_mode()
    else:
        main()
