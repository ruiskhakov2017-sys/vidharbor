"""Real T501.2 HTTP/DB/AMQP/FFmpeg checks on the dedicated vidharbor-t5012 stand."""

import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright

from smoke_s04 import BASE, login_via_ui, require, upload_http, verify_playback


COMPOSE = ["docker", "compose", "--env-file", ".env.example"]


def compose(*args, timeout=120, **kwargs):
    return subprocess.check_output([*COMPOSE, *args], text=True, timeout=timeout, **kwargs).strip()


def database(code):
    return json.loads(compose("exec", "-T", "upload", "python", "manage.py", "shell", "-c", code).splitlines()[-1])


def snapshot(video_id):
    uuid.UUID(video_id)
    return database(
        "import json; from vidharbor.models import Video, Job, Upload, Outbox; "
        "from vidharbor.storage import original_path, file_digest; "
        f"v=Video.objects.get(pk={video_id!r}); p=original_path(v.upload_id); "
        "print(json.dumps({'id':str(v.id),'generation':v.generation,'state':v.state,"
        "'upload':Upload.objects.values().get(pk=v.upload_id),'hash':file_digest(p),"
        "'inode':p.stat().st_ino,'mtime':p.stat().st_mtime_ns,"
        "'jobs':list(Job.objects.filter(video=v).order_by('generation').values()),"
        "'outbox':list(Outbox.objects.filter(job__video=v).order_by('job__generation','attempt_no').values())},default=str))"
    )


def wait_for(check, timeout=150):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = check()
        if result:
            return result
        time.sleep(.5)
    raise AssertionError("Timed out waiting for the real service/job checkpoint")


def wait_failed(video_id, generation):
    def check():
        value = snapshot(video_id)
        return value if value["generation"] == generation and value["jobs"][-1]["state"] == "FAILED" else None
    value = wait_for(check)
    reason = database(
        "import json; from vidharbor.models import Attempt; "
        f"print(json.dumps(Attempt.objects.filter(job__video_id={video_id!r},job__generation={generation})"
        ".order_by('-number').values_list('reason',flat=True).first()))"
    )
    assert reason == "ffmpeg_timeout", reason
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    assert os.getenv("COMPOSE_PROJECT_NAME", "").startswith("vidharbor-t5012")
    assert os.getenv("VIDHARBOR_DB_NAME") == "vidharbor_s04_test"
    expect.set_options(timeout=45000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=os.getenv("VIDHARBOR_CHROME", "/usr/bin/chromium"))
        page = browser.new_page()
        compose("stop", "--timeout", "8", "worker", "relay")
        compose("start", "rabbitmq")
        wait_for(lambda: page.request.get(BASE + "/health/ready").status == 200)
        login_via_ui(page, "retry-" + uuid.uuid4().hex[:10], uuid.uuid4().hex)
        first = upload_http(page.request, args.source, "Ручной повтор " + uuid.uuid4().hex[:8])
        video_id, first_job = first["video_id"], first["job_id"]
        route = f"{BASE}/api/videos/{video_id}/jobs/{first_job}/retry"
        watch = f"/watch/{video_id}"
        master = f"/media/{video_id}/master.m3u8"
        compose("up", "-d", "--no-deps", "relay", "worker", env={**os.environ, "VIDHARBOR_FFMPEG_TIMEOUT": "1"})
        initial = wait_failed(video_id, 1)
        original = {key: initial[key] for key in ("upload", "hash", "inode", "mtime")}
        print(f"Real FFmpeg deadline -> FAILED generation 1: video={video_id}, job={first_job}", flush=True)
        compose("stop", "--timeout", "8", "worker", "relay", "rabbitmq")
        page.reload()
        expect(page.get_by_role("button", name="Повторить обработку", exact=True)).to_be_visible()

        lost = []

        def lose_reply(intercept):
            response = intercept.fetch()
            assert response.status == 201, response.text()
            lost.append(response.json()["job"])
            intercept.abort()

        page.route("**/api/videos/*/jobs/*/retry", lose_reply, times=1)
        page.get_by_role("button", name="Повторить обработку", exact=True).click()
        expect(page.get_by_role("button", name="Повторить запрос", exact=True)).to_be_enabled()
        second_job = lost[0]["id"]
        csrf = require(page.request.get(BASE + "/api/auth/session")).json()["csrf_token"]
        results = page.evaluate("""async ({url,csrf}) => Promise.all(Array.from({length:4},async () => {
            const response=await fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf}});
            return {status:response.status,body:await response.json()};
        }))""", {"url": route, "csrf": csrf})
        assert all(result["status"] == 200 and result["body"]["job"]["id"] == second_job for result in results), results
        queued = snapshot(video_id)
        assert queued["generation"] == 2 and len(queued["jobs"]) == 2
        command = next(entry for entry in queued["outbox"] if entry["job_id"] == second_job and entry["kind"] == "video.transcode.v1")
        assert command["sent_at"] is None
        compose("restart", "upload")
        wait_for(lambda: compose("exec", "-T", "upload", "python", "-c",
            "import urllib.request\ntry:\n print(urllib.request.urlopen('http://127.0.0.1:8000/health/live',timeout=5).status)"
            "\nexcept OSError:\n print(0)") == "200")
        # Rootless Docker can assign a new IP after restart; refresh Nginx's existing static upstream.
        compose("exec", "-T", "web", "nginx", "-s", "reload")
        receipt = require(page.request.post(route, headers={"X-CSRFToken": csrf})).json()
        assert receipt["job"]["id"] == second_job
        compose("start", "rabbitmq")
        wait_for(lambda: page.request.get(BASE + "/health/ready").status == 200)
        compose("up", "-d", "--no-deps", "relay", "worker", env={**os.environ, "VIDHARBOR_FFMPEG_TIMEOUT": "1"})
        failed = wait_failed(video_id, 2)
        assert next(entry for entry in failed["outbox"] if entry["message_id"] == command["message_id"])["sent_at"]
        print(f"Lost reply + 4 replays + upload restart + broker outage: same generation 2 job={second_job}, message={command['message_id']}", flush=True)
        expect(page.get_by_role("button", name="Повторить запрос", exact=True)).to_be_enabled()
        page.get_by_role("button", name="Повторить запрос", exact=True).click()
        expect(page.get_by_role("button", name="Повторить обработку", exact=True)).to_be_enabled()
        assert len(snapshot(video_id)["jobs"]) == 2
        print("Late UI retry after successor FAILED returned that same failed job; no generation 3 created.", flush=True)

        compose("stop", "--timeout", "8", "worker")
        compose("up", "-d", "--no-deps", "worker", env={**os.environ, "VIDHARBOR_FFMPEG_TIMEOUT": "10800"})
        results = page.evaluate("""async ({url,csrf}) => Promise.all(Array.from({length:4},async () => {
            const response=await fetch(url,{method:'POST',headers:{'X-CSRFToken':csrf}});
            return {status:response.status,body:await response.json()};
        }))""", {"url": f"{BASE}/api/videos/{video_id}/jobs/{second_job}/retry", "csrf": csrf})
        assert sorted(result["status"] for result in results) == [200, 200, 200, 201], results
        assert len({result["body"]["job"]["id"] for result in results}) == 1
        print("4 concurrent real HTTP creation requests: one 201, three 200, one generation 3 job.", flush=True)
        page.reload()
        expect(page.get_by_role("link", name="Готово · Смотреть", exact=True)).to_have_attribute("href", watch, timeout=180000)
        final = snapshot(video_id)
        assert final["generation"] == 3 and len(final["jobs"]) == 3
        assert {key: final[key] for key in original} == original
        assert final["jobs"][0] == initial["jobs"][0]
        assert final["jobs"][1] == failed["jobs"][1]
        receipt = require(page.request.post(route, headers={"X-CSRFToken": csrf})).json()
        assert receipt["job"]["id"] == second_job and receipt["job"]["state"] == "FAILED"
        last_job = final["jobs"][-1]["id"]
        require(page.request.post(f"{BASE}/api/videos/{video_id}/jobs/{last_job}/retry", headers={"X-CSRFToken": csrf}), 409)
        detail = require(page.request.get(f"{BASE}/api/catalog/{video_id}")).json()
        assert detail["watch_url"] == watch and detail["playlist_url"] == master
        response = require(page.request.get(BASE + master))
        variants = [line for line in response.text().splitlines() if line and not line.startswith("#")]
        assert len(variants) == 3 and all(uri.startswith("g3/") for uri in variants)
        segments = 0
        for uri in variants:
            playlist = require(page.request.get(urljoin(response.url, uri)))
            for segment in [line for line in playlist.text().splitlines() if line and not line.startswith("#")]:
                assert require(page.request.get(urljoin(playlist.url, segment))).body()
                segments += 1
        verify_playback(page, detail, [(640, 360), (852, 480), (1280, 720)])
        final_again = snapshot(video_id)
        assert final_again["generation"] == 3 and len(final_again["jobs"]) == 3
        assert {key: final_again[key] for key in original} == original
        print(f"Fixed deadline -> READY g3, unchanged watch/master/upload/hash/inode/mtime; {segments} HTTP segments; Auto and all 3 qualities played.", flush=True)
        print(json.dumps({"video_id":video_id,"jobs":final_again["jobs"],"original_sha256":original["hash"]}), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
