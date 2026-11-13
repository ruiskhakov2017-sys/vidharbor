"""Verify rename and stale polling after a real S04 upload on a disposable Compose stand."""
import argparse
import json
import os
import subprocess
import uuid
import time
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from smoke_s04 import BASE, login_via_ui, require, upload_http, verify_playback


def database_snapshot(video_id):
    uuid.UUID(video_id)
    sql = f"""
    SELECT json_build_object(
        'video', to_jsonb(v) - 'title',
        'upload', to_jsonb(u),
        'jobs', (SELECT jsonb_agg(to_jsonb(j) ORDER BY j.id) FROM vidharbor_job j WHERE j.video_id=v.id),
        'attempts', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.id) FROM vidharbor_attempt a
            JOIN vidharbor_job j ON j.id=a.job_id WHERE j.video_id=v.id),
        'outbox', (SELECT jsonb_agg(to_jsonb(o) ORDER BY o.message_id) FROM vidharbor_outbox o
            JOIN vidharbor_job j ON j.id=o.job_id WHERE j.video_id=v.id)
    ) FROM vidharbor_video v JOIN vidharbor_upload u ON u.id=v.upload_id WHERE v.id='{video_id}';
    """
    compose = ["docker", "compose", "--env-file", ".env.example", "exec", "-T"]
    result = json.loads(subprocess.check_output([
        *compose, "db", "psql", "-U", os.getenv("VIDHARBOR_DB_USER", "vidharbor"),
        "-d", os.getenv("VIDHARBOR_DB_NAME", "vidharbor"), "-At", "-c", sql,
    ], timeout=60))
    upload_id = str(uuid.UUID(result["upload"]["id"]))
    digest = subprocess.check_output(
        [*compose, "upload", "sha256sum", f"/var/lib/vidharbor/original/{upload_id}"], timeout=60,
    ).decode().split()[0]
    assert digest == result["upload"]["sha256"]
    return result, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    expect.set_options(timeout=45000)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome")
        owner = browser.new_page()
        guest = browser.new_page()
        other = browser.new_page()
        suffix = uuid.uuid4().hex[:8]
        old_title = f"Публикация {suffix}"
        new_title = f"Новое название {suffix}"
        login_via_ui(owner, f"rename-owner-{suffix}", uuid.uuid4().hex)
        login_via_ui(other, f"rename-other-{suffix}", uuid.uuid4().hex)
        job = upload_http(owner.request, args.source, old_title)
        video_id = job["video_id"]
        owner.reload(wait_until="domcontentloaded")
        row = owner.get_by_role("listitem").filter(has=owner.get_by_text(old_title, exact=True))
        expect(row.get_by_role("link", name="Готово · Смотреть")).to_have_attribute(
            "href", f"/watch/{video_id}", timeout=180000,
        )
        before, digest = database_snapshot(video_id)
        ready_events = [item for item in before["outbox"] if item["kind"] == "video.ready"]
        assert len(ready_events) == 1 and ready_events[0]["payload"]["title"] == old_title
        assert ready_events[0]["sent_at"] is None
        detail_url = f"{BASE}/api/catalog/{video_id}"
        old_public = require(guest.request.get(detail_url)).json()
        old_master = require(guest.request.get(BASE + old_public["playlist_url"])).body()
        rename_url = f"{BASE}/api/videos/{video_id}/rename"
        other_token = require(other.request.get(BASE + "/api/auth/session")).json()["csrf_token"]
        require(other.request.post(rename_url, data={"title": "Чужое"}, headers={"X-CSRFToken": other_token}), 404)
        require(owner.request.post(rename_url, data={"title": "Без CSRF"}), 403)

        # The other item only keeps cabinet polling active. The ready item and stale title
        # come from real PostgreSQL responses; hold one response until rename succeeds.
        stale = []
        observed = []
        def intercept(route):
            response = route.fetch()
            body = response.json()
            observed.append(body)
            if len(observed) == 2:
                stale.append((route, response, body))
                return
            body["videos"].append({"id": "poll-fixture", "title": "Ожидает", "state": "QUEUED"})
            route.fulfill(response=response, json=body)

        owner.route("**/api/videos", intercept)
        owner.reload(wait_until="domcontentloaded")
        owner.wait_for_function("document.querySelector('.videos')?.textContent.includes('Ожидает')")
        expect(owner.get_by_text(old_title, exact=True)).to_be_visible()
        # Polling runs every 3s. Its request arrives while the rename form is open.
        row.get_by_role("button", name="Переименовать").click()
        owner.get_by_label("Новое название", exact=True).fill(new_title)
        limit = time.monotonic() + 20
        while not stale and time.monotonic() < limit:
            owner.wait_for_timeout(100)
        assert stale and stale[0][2]["videos"][0]["title"] == old_title
        with owner.expect_response(lambda response: response.url == rename_url) as renamed:
            owner.get_by_role("button", name="Сохранить", exact=True).click()
        assert require(renamed.value).json() == {
            "id": video_id, "title": new_title, "state": "READY", "watch_url": f"/watch/{video_id}",
        }
        expect(owner.get_by_role("status")).to_have_text("Название сохранено")
        delayed, response, body = stale[0]
        with owner.expect_response(lambda result: result.url == BASE + "/api/videos"):
            delayed.fulfill(response=response, json=body)
        owner.wait_for_function("performance.getEntriesByName(location.origin + '/api/videos').length >= 2")
        expect(owner.get_by_text(new_title, exact=True)).to_be_visible()
        expect(owner.get_by_text(old_title, exact=True)).to_have_count(0)
        expect(owner.get_by_role("link", name="Готово · Смотреть")).to_have_attribute("href", f"/watch/{video_id}")
        owner.unroute_all(behavior="wait")
        owner.reload(wait_until="domcontentloaded")
        expect(owner.get_by_text(new_title, exact=True)).to_be_visible()

        new_public = require(guest.request.get(detail_url)).json()
        assert new_public == {**old_public, "title": new_title}
        assert require(guest.request.get(BASE + "/api/catalog", params={"q": old_title})).json() == {"videos": []}
        assert require(guest.request.get(BASE + "/api/catalog", params={"q": new_title.swapcase()})).json() == {
            "videos": [new_public],
        }
        assert require(guest.request.get(BASE + new_public["playlist_url"])).body() == old_master
        verify_playback(guest, new_public, [(640, 360), (852, 480), (1280, 720)])
        after, after_digest = database_snapshot(video_id)
        assert (after, after_digest) == (before, digest)
        print(
            f"PASS READY rename video={video_id} job={job['job_id']} event={ready_events[0]['message_id']} "
            f"sha256={digest}; stale poll ignored; status, links, HLS, search, CSRF and ownership verified",
            flush=True,
        )
        browser.close()


if __name__ == "__main__":
    main()
