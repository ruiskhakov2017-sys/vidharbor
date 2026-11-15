"""Delete through the real UI while FFmpeg is running, then resume cleanup."""
import argparse
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from smoke_s04 import BASE, login_via_ui, require
from smoke_completion import database

COMPOSE = ["docker", "compose", "--env-file", ".env.example"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    assert os.environ.get("COMPOSE_PROJECT_NAME", "").startswith("vidharbor-")
    report = {"status": "RUNNING"}
    subprocess.run([*COMPOSE, "stop", "cleanup"], check=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/chromium", args=["--no-sandbox"])
            page = browser.new_page(); expect.set_options(timeout=30000)
            login_via_ui(page, "delete-" + uuid.uuid4().hex[:10], uuid.uuid4().hex)
            page.get_by_label("Название видео").fill("Удаление во время обработки")
            page.get_by_label("Видеофайл").set_input_files(args.source.resolve())
            with page.expect_response(lambda r: r.url.endswith("/complete"), timeout=120000) as complete:
                page.get_by_role("button", name="Загрузить", exact=True).click()
            uploaded = require(complete.value).json(); video_id = uploaded["video_id"]
            deadline = time.monotonic() + 30
            while True:
                state = require(page.request.get(BASE + "/api/videos")).json()["videos"][0]["state"]
                if state == "PROCESSING": break
                assert state != "READY", "Fixture finished before the processing-deletion race could be tested"
                assert time.monotonic() < deadline
                page.wait_for_timeout(50)
            csrf = require(page.request.get(BASE + "/api/auth/session")).json()["csrf_token"]
            page.on("dialog", lambda dialog: dialog.accept())
            with page.expect_response(lambda r: r.url.endswith("/delete")) as deletion:
                page.get_by_role("button", name="Удалить", exact=True).click()
            response = deletion.value
            assert response.status == 202, (response.status, response.text())
            report["first_delete"] = response.json()
            for path in (f"/watch/{video_id}", f"/media/{video_id}/master.m3u8", f"/media/{video_id}/g1/v0/seg-000000.ts"):
                require(page.request.get(BASE + path), 404)
            page.wait_for_timeout(1500)
            pending = database("import json; from vidharbor.models import Video,Job,Outbox; from vidharbor.storage import original_path; "
                f"v=Video.objects.get(pk={video_id!r}); print(json.dumps({{'state':v.state,'generation':v.generation,"
                "'original_exists':original_path(v.upload_id).exists(),'ready_events':Outbox.objects.filter(job__video=v,kind='video.ready').count(),"
                "'job_state':Job.objects.get(video=v).state}))")
            assert pending["state"] == "DELETING" and pending["original_exists"]
            assert pending["job_state"] == "CANCELLED" and pending["ready_events"] == 0
            report["after_worker_cancel_before_cleanup_restart"] = pending
            subprocess.run([*COMPOSE, "restart", "cleanup"], check=True)
            deadline = time.monotonic() + 30
            while require(page.request.get(BASE + "/api/videos")).json()["videos"]:
                assert time.monotonic() < deadline
                page.wait_for_timeout(250)
            repeated = require(page.request.post(BASE + f"/api/videos/{video_id}/delete", headers={"X-CSRFToken":csrf})).json()
            assert repeated["state"] == "DELETED"
            report["final"] = database("import json; from vidharbor.models import Video; from vidharbor.storage import original_path; "
                "from vidharbor.publication import staging_path,ready_path; from vidharbor import journal; "
                f"v=Video.objects.get(pk={video_id!r}); print(json.dumps({{'state':v.state,'generation':v.generation,"
                "'original_exists':original_path(v.upload_id).exists(),'processing_exists':staging_path(v.id).exists(),"
                "'ready_exists':ready_path(v.id,1).parent.exists(),"
                "'deletion_entries':sum(e['kind']=='delete' and e['key']==str(v.id) for e in journal.read_entries()[0])}))")
            assert report["final"] == {"state":"DELETED","generation":pending["generation"],"original_exists":False,
                "processing_exists":False,"ready_exists":False,"deletion_entries":1}
            report.update(status="PASS", video_id=video_id)
            browser.close()
    except Exception as exc:
        report.update(status="FAIL", error=repr(exc)); raise
    finally:
        subprocess.run([*COMPOSE, "start", "cleanup"], check=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__": main()
