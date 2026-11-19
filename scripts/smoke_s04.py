"""Check the S04 upload, public HLS and desktop browsers against a running Compose project."""

import argparse
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urljoin

from playwright.sync_api import expect, sync_playwright


BASE = f"http://127.0.0.1:{os.getenv('VIDHARBOR_PORT', '8085')}"
FIXTURES = {
    "landscape.mp4": ("Горизонт", [(640, 360), (852, 480), (1280, 720)], True),
    "rotated.mov": ("Портрет", [(360, 640), (480, 852), (720, 1280)], False),
    "small.webm": ("Малый", [(320, 180)], False),
    "sample.mkv": ("Матроска", [(640, 360), (852, 480), (1280, 720)], True),
}


def launch_browser(playwright, name):
    """Allow explicit cloud browser binaries without replacing browser engines."""
    if name == "Firefox":
        return playwright.firefox.launch()
    executable = os.getenv("VIDHARBOR_" + name.upper())
    return (playwright.chromium.launch(executable_path=executable, args=["--no-sandbox"])
            if executable else playwright.chromium.launch(channel={"Chrome": "chrome", "Edge": "msedge"}[name]))


def require(response, expected=200):
    assert response.status == expected, (response.status, response.url, response.text()[:800])
    return response


def login_via_ui(page, username, password):
    page.goto(BASE + "/studio", wait_until="domcontentloaded")
    page.get_by_role("button", name="Создать аккаунт").click()
    page.get_by_label("Имя пользователя").fill(username)
    page.get_by_label("Пароль").fill(password)
    page.get_by_role("button", name="Зарегистрироваться").click()
    expect(page.get_by_text("Аккаунт создан. Теперь войдите.")).to_be_visible()
    page.get_by_label("Имя пользователя").fill(username)
    page.get_by_label("Пароль").fill(password)
    page.get_by_role("button", name="Войти").click()
    expect(page.get_by_role("heading", name=re.compile("Кабинет"))).to_be_visible()


def upload_http(request, path, title):
    csrf = require(request.get(BASE + "/api/auth/session")).json()["csrf_token"]
    headers = {"X-CSRFToken": csrf}
    data = path.read_bytes()
    upload = require(request.post(
        BASE + "/api/uploads", data={"title": title, "size": len(data)}, headers=headers,
    ), 201).json()
    route = BASE + "/api/uploads/" + upload["id"]
    require(request.put(route + "/content", data=data, headers={
        **headers, "Content-Type": "application/octet-stream",
    }))
    return require(request.post(route + "/complete", headers=headers)).json()


def upload_and_wait(playwright, folder):
    browser = launch_browser(playwright, "Chrome")
    try:
        suffix = uuid.uuid4().hex[:8]
        page = browser.new_page()
        login_via_ui(page, "s04-" + suffix, uuid.uuid4().hex)
        filename = "landscape.mp4"
        title = FIXTURES[filename][0] + " " + suffix
        upload_button = page.get_by_role("button", name="Загрузить")
        expect(upload_button).to_be_enabled()
        page.get_by_label("Название видео").fill(title)
        page.get_by_label("Видеофайл").set_input_files(folder / filename)
        assert page.locator("form").evaluate("(form) => form.checkValidity()")
        with page.expect_response(lambda response: response.url.endswith("/complete"), timeout=120000) as result:
            upload_button.click()
        response = result.value
        assert response.ok, response.text()
        jobs = {filename: response.json()}
        expect(page.get_by_role("link", name="Готово · Смотреть")).to_be_visible(timeout=180000)
        for filename, (label, _, _) in list(FIXTURES.items())[1:]:
            jobs[filename] = upload_http(page.request, folder / filename, label + " " + suffix)
        page.reload(wait_until="domcontentloaded")
        expect(page.get_by_role("link", name="Готово · Смотреть")).to_have_count(4, timeout=220000)
        print(f"Chrome upload and cabinet READY; fixture jobs: {json.dumps(jobs)}", flush=True)
        return jobs, suffix
    finally:
        browser.close()


def verify_hls(request, filename, job, title):
    _, sizes, has_audio = FIXTURES[filename]
    video_id = job["video_id"]
    detail = require(request.get(f"{BASE}/api/catalog/{video_id}")).json()
    assert detail["title"] == title
    master = require(request.get(BASE + detail["playlist_url"]))
    assert "application/vnd.apple.mpegurl" in master.headers["content-type"]
    assert "no-store" in master.headers["cache-control"]
    assert [tuple(map(int, pair)) for pair in re.findall(r"RESOLUTION=(\d+)x(\d+)", master.text())] == sizes
    uris = [line for line in master.text().splitlines() if line and not line.startswith("#")]
    assert len(uris) == len(sizes)
    with tempfile.TemporaryDirectory() as directory:
        for index, (uri, size) in enumerate(zip(uris, sizes, strict=True)):
            assert uri.startswith("g1/")
            playlist = require(request.get(urljoin(master.url, uri)))
            assert "#EXT-X-ENDLIST" in playlist.text()
            segments = [line for line in playlist.text().splitlines() if line and not line.startswith("#")]
            assert segments
            for segment in segments:
                response = require(request.get(urljoin(playlist.url, segment)))
                assert "video/mp2t" in response.headers["content-type"]
                assert response.body()
                if segment == segments[0]:
                    target = Path(directory) / f"variant-{index}.ts"
                    target.write_bytes(response.body())
                    streams = json.loads(subprocess.check_output([
                        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(target),
                    ]))["streams"]
                    video = next(stream for stream in streams if stream["codec_type"] == "video")
                    assert video["codec_name"] == "h264" and (video["width"], video["height"]) == size
                    audio = [stream["codec_name"] for stream in streams if stream["codec_type"] == "audio"]
                    assert audio == (["aac"] if has_audio else []), audio
    for path in (
        f"/media/{video_id}/g99/v0/index.m3u8",
        f"/_vidharbor_hls/{video_id}/g1/master.m3u8",
        f"/original/{video_id}",
        f"/processing/{video_id}/master.m3u8",
    ):
        require(request.get(BASE + path), 404)
    print(f"{filename}: HTTP playlists and all segments; decoded {sizes}; audio={has_audio}", flush=True)
    return detail


def verify_playback(page, detail, sizes):
    page.goto(BASE + detail["watch_url"], wait_until="domcontentloaded")
    expect(page.get_by_role("heading", name=detail["title"])).to_be_visible()
    quality = page.get_by_label("Качество")
    expect(quality.locator("option")).to_have_count(len(sizes) + 1, timeout=45000)
    expect(quality).to_have_value("-1")
    video = page.locator("video")
    video.evaluate("""(video) => {
        video.muted = true;
        return Promise.race([video.play(), new Promise((_, reject) =>
            setTimeout(() => reject(new Error('play() did not resolve within 45 seconds')), 45000))]);
    }""")
    page.wait_for_function("document.querySelector('video').currentTime > 0.3", timeout=45000)

    for index, dimensions in enumerate(sizes):
        quality.select_option(str(index))
        video.evaluate("""(video) => {
            video.currentTime = 0;
            return Promise.race([video.play(), new Promise((_, reject) =>
                setTimeout(() => reject(new Error('play() did not resolve within 45 seconds')), 45000))]);
        }""")
        page.wait_for_function(
            """([width, height]) => {
                const video = document.querySelector("video");
                return video.videoWidth === width && video.videoHeight === height && video.currentTime > 0.25;
            }""",
            arg=dimensions,
            timeout=45000,
        )
    quality.select_option("-1")
    expect(quality).to_have_value("-1")
    assert video.evaluate("(video) => !video.error")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixtures", type=Path)
    parser.add_argument("--state", type=Path, help="Reuse jobs recorded on this Compose stand.")
    args = parser.parse_args()
    expect.set_options(timeout=45000)
    with sync_playwright() as playwright:
        if args.state and args.state.is_file():
            jobs, suffix = json.loads(args.state.read_text(encoding="utf-8"))
        else:
            jobs, suffix = upload_and_wait(playwright, args.fixtures.resolve())
            if args.state:
                args.state.write_text(json.dumps([jobs, suffix]), encoding="utf-8")
        guest = playwright.request.new_context()
        try:
            details = {
                filename: verify_hls(guest, filename, jobs[filename], label + " " + suffix)
                for filename, (label, _, _) in FIXTURES.items()
            }
            result = require(guest.get(BASE + "/api/catalog", params={"q": "гОРИЗОНТ " + suffix})).json()
            assert result == {"videos": [details["landscape.mp4"]]}, result
            absent = str(uuid.uuid4())
            for path in (f"/watch/{absent}", f"/media/{absent}/master.m3u8", f"/api/catalog/{absent}"):
                require(guest.get(BASE + path), 404)
        finally:
            guest.dispose()

        for name in ("Chrome", "Edge", "Firefox"):
            browser = launch_browser(playwright, name)
            try:
                page = browser.new_page()
                page.goto(BASE + "/catalog", wait_until="domcontentloaded")
                page.get_by_role("searchbox", name="Поиск видео").fill("гОРИЗОНТ " + suffix)
                page.get_by_role("button", name="Найти").click()
                page.get_by_role("link", name=details["landscape.mp4"]["title"]).click()
                expect(page.get_by_role("heading", name=details["landscape.mp4"]["title"])).to_be_visible()
                for filename in ("landscape.mp4", "rotated.mov", "small.webm"):
                    verify_playback(page, details[filename], FIXTURES[filename][1])
                print(f"{name} {browser.version}: guest search and direct link, Auto and every level for 3 clips", flush=True)
            finally:
                browser.close()


if __name__ == "__main__":
    main()
