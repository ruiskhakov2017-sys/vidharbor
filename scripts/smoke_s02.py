"""Verify S02 against a running test Compose project with installed Chrome and Playwright."""

import argparse
import hashlib
import os
import subprocess
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = f"http://127.0.0.1:{os.getenv('VIDHARBOR_PORT', '8085')}"
COMPOSE = ["docker", "compose", "--env-file", ".env.example", "exec", "-T"]


def exec_service(*args):
    return subprocess.check_output([*COMPOSE, *args], text=True, timeout=30).strip()


def register_and_sign_in(page, username, password):
    page.goto(BASE_URL)
    page.get_by_role("button", name="Создать аккаунт").click()
    page.get_by_label("Имя пользователя").fill(username)
    page.get_by_label("Пароль", exact=True).fill(password)
    page.get_by_role("button", name="Зарегистрироваться").click()
    expect(page.get_by_role("status")).to_have_text("Аккаунт создан. Теперь войдите.")
    page.get_by_label("Имя пользователя").fill(username)
    page.get_by_label("Пароль", exact=True).fill(password)
    page.get_by_role("button", name="Войти", exact=True).click()
    expect(page.get_by_role("heading", name=f"Кабинет · {username}")).to_be_visible()


def throttle_upload(page):
    cdp = page.context.new_cdp_session(page)
    cdp.send("Network.enable")
    cdp.send("Network.emulateNetworkConditions", {
        "offline": False, "latency": 20,
        "downloadThroughput": 1024 * 1024, "uploadThroughput": 192 * 1024,
    })


def abort_video(page, source):
    throttle_upload(page)
    page.evaluate("""() => {
        const send = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.send = function (body) {
            window.activeUpload = this;
            return send.call(this, body);
        };
    }""")
    page.get_by_label("Название видео").fill("Прерванное видео")
    page.get_by_label("Видеофайл").set_input_files(source)
    with page.expect_response(lambda response: response.url.endswith("/api/uploads")) as observed:
        page.get_by_role("button", name="Загрузить", exact=True).click()
    upload_id = observed.value.json()["id"]
    page.wait_for_function("document.querySelector('progress')?.value > 0")
    page.evaluate("window.activeUpload.abort()")
    expect(page.get_by_role("alert")).to_contain_text("Передача прервалась")
    expect(page.get_by_text("Прерванное видео", exact=True)).to_have_count(0)
    state = page.request.get(f"{BASE_URL}/api/uploads/{upload_id}").json()["state"]
    assert state != "COMPLETE"
    return upload_id


def upload_video(page, title, source, *, lose_reply=False):
    throttle_upload(page)
    page.evaluate("""() => {
        window.uploadSamples = [];
        window.verificationSeen = false;
        new MutationObserver(() => {
            const progress = document.querySelector('progress');
            if (progress) window.uploadSamples.push(progress.value);
            if (document.querySelector('[role=status]')?.textContent === 'Проверяем файл')
                window.verificationSeen = true;
        }).observe(document.querySelector('main'), {
            childList: true, subtree: true, attributes: true, characterData: true
        });
    }""")
    page.get_by_label("Название видео").fill(title)
    page.get_by_label("Видеофайл").set_input_files(source)
    first_reply = []
    if lose_reply:
        def drop_response(route):
            saved = route.fetch()
            assert saved.ok, saved.text()
            first_reply.append(saved.json())
            route.abort()

        page.route("**/api/uploads/*/complete", drop_response, times=1)
        page.get_by_role("button", name="Загрузить", exact=True).click()
        button = page.get_by_role("button", name="Повторить проверку")
        expect(button).to_be_visible(timeout=90000)
    else:
        button = page.get_by_role("button", name="Загрузить", exact=True)

    with page.expect_response(lambda response: response.url.endswith("/complete"), timeout=90000) as observed:
        button.click()
    response = observed.value
    if lose_reply:
        assert first_reply == [response.json()], first_reply
    assert response.ok, response.text()
    expect(page.get_by_text("Видео принято и ожидает обработки", exact=True)).to_be_visible()
    expect(page.get_by_text(title, exact=True)).to_be_visible()
    samples = sorted(set(page.evaluate("window.uploadSamples")))
    assert 100 in samples and page.evaluate("window.verificationSeen"), samples
    upload_id = str(uuid.UUID(response.url.split("/")[-2]))
    return upload_id, response.json(), samples


def csrf(context):
    token = context.request.get(BASE_URL + "/api/auth/session").json()["csrf_token"]
    return {"X-CSRFToken": token}


def check_saved(source, upload_id, result):
    with source.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    saved = exec_service("upload", "sha256sum", f"/var/lib/vidharbor/original/{upload_id}").split()[0]
    assert saved == digest, (saved, digest)
    video_id = str(uuid.UUID(result["video_id"]))
    job_id = str(uuid.UUID(result["job_id"]))
    message_id = str(uuid.UUID(result["message_id"]))
    sql = (
        "SELECT COUNT(DISTINCT j.id), COUNT(o.message_id), "
        "COUNT(o.message_id) FILTER (WHERE o.sent_at IS NULL) "
        "FROM vidharbor_job j LEFT JOIN vidharbor_outbox o ON o.job_id = j.id "
        f"WHERE j.video_id = '{video_id}'; "
        "SELECT j.id, o.message_id FROM vidharbor_job j "
        "JOIN vidharbor_outbox o ON o.job_id = j.id "
        f"WHERE j.video_id = '{video_id}';"
    )
    user = os.getenv("VIDHARBOR_DB_USER", "vidharbor")
    database = os.getenv("VIDHARBOR_DB_NAME", "vidharbor")
    rows = exec_service("db", "psql", "-U", user, "-d", database, "-At", "-c", sql).splitlines()
    assert rows == ["1|1|1", f"{job_id}|{message_id}"], rows
    print(f"{source.name}: bytes={source.stat().st_size}, sha256={digest}, jobs=1, pending_outbox=1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first_video", type=Path)
    parser.add_argument("second_video", type=Path)
    args = parser.parse_args()
    suffix = uuid.uuid4().hex[:8]
    password = uuid.uuid4().hex

    expect.set_options(timeout=25000)
    with sync_playwright() as playwright:
        chrome = os.getenv("VIDHARBOR_CHROME")
        browser = playwright.chromium.launch(**({"executable_path": chrome} if chrome else {"channel": "chrome"}))
        try:
            print(f"Chrome {browser.version}")
            context_a, context_b = browser.new_context(), browser.new_context()
            page_a, page_b = context_a.new_page(), context_b.new_page()
            for page, name in ((page_a, "author-a"), (page_b, "author-b")):
                register_and_sign_in(page, f"{name}-{suffix}", password)
            aborted = abort_video(page_a, args.first_video)
            id_a, result_a, progress_a = upload_video(page_a, "Видео автора A", args.first_video, lose_reply=True)
            assert aborted != id_a
            assert any(0 < value < 100 for value in progress_a), progress_a
            id_b, result_b, progress_b = upload_video(page_b, "Видео автора B", args.second_video)

            page_a.reload()
            page_b.reload()
            expect(page_a.get_by_text("Видео автора A", exact=True)).to_be_visible()
            expect(page_b.get_by_text("Видео автора B", exact=True)).to_be_visible()
            expect(page_a.get_by_text("Видео автора B", exact=True)).to_have_count(0)
            expect(page_b.get_by_text("Видео автора A", exact=True)).to_have_count(0)
            assert context_b.request.get(f"{BASE_URL}/api/uploads/{id_a}").status == 404
            assert context_b.request.post(f"{BASE_URL}/api/uploads/{id_a}/complete", headers=csrf(context_b)).status == 404
            assert context_a.request.post(f"{BASE_URL}/api/uploads/{id_a}/complete").status == 403
            for _ in range(2):
                repeated = context_a.request.post(f"{BASE_URL}/api/uploads/{id_a}/complete", headers=csrf(context_a))
                assert repeated.ok and repeated.json() == result_a, repeated.text()
            for context, upload_id in ((context_a, id_a), (context_b, id_b)):
                assert context.request.get(f"{BASE_URL}/original/{upload_id}").status == 404
            check_saved(args.first_video, id_a, result_a)
            check_saved(args.second_video, id_b, result_b)
            page_a.get_by_role("button", name="Выйти").click()
            expect(page_a.get_by_role("heading", name="Вход")).to_be_visible()
            assert context_a.request.get(BASE_URL + "/api/videos").status == 401
            print(f"Browser progress: author A {progress_a}; author B {progress_b}")
            print("Two sessions, owner checks, CSRF, abort and new upload, lost reply and identical IDs on retry: passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
