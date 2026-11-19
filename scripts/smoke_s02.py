"""Verify S02 on a disposable Compose stack with running relay and no workers.

Requires Chrome and scripts/requirements-smoke.txt. Real video.jobs deliveries
are inspected without ack and requeued; do not run against a shared queue.
"""

import argparse
import hashlib
import json
import os
import subprocess
import textwrap
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = f"http://127.0.0.1:{os.getenv('VIDHARBOR_PORT', '8085')}"
COMPOSE = ["docker", "compose", "--env-file", ".env.example", "exec", "-T"]


def exec_service(*args):
    return subprocess.check_output([*COMPOSE, *args], text=True, timeout=30).strip()


def register_and_sign_in(page, username, password):
    page.goto(BASE_URL + "/studio")
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
        "SELECT COUNT(DISTINCT j.id), COUNT(o.message_id) "
        "FROM vidharbor_job j LEFT JOIN vidharbor_outbox o ON o.job_id = j.id "
        f"WHERE j.video_id = '{video_id}'; "
        "SELECT j.id, o.message_id FROM vidharbor_job j "
        "JOIN vidharbor_outbox o ON o.job_id = j.id "
        f"WHERE j.video_id = '{video_id}'; "
        "SELECT u.id, u.size, u.sha256, u.state FROM vidharbor_upload u "
        "JOIN vidharbor_video v ON v.upload_id = u.id "
        f"WHERE v.id = '{video_id}';"
    )
    user = os.getenv("VIDHARBOR_DB_USER", "vidharbor")
    database = os.getenv("VIDHARBOR_DB_NAME", "vidharbor")
    rows = exec_service("db", "psql", "-U", user, "-d", database, "-At", "-c", sql).splitlines()
    assert rows == [
        "1|1", f"{job_id}|{message_id}",
        f"{upload_id}|{source.stat().st_size}|{digest}|COMPLETE",
    ], rows
    print(f"{source.name}: bytes={source.stat().st_size}, sha256={digest}, jobs=1, outbox=1")


def check_delivery(uploads):
    """Inspect real RabbitMQ messages on a disposable stack with relay and no worker.

    Keep deliveries unacked while inspecting; requeue all of them in finally.
    Do not create another binding that could hide a broken production route.
    """
    code = textwrap.dedent('''
        import json, os, sys, time
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vidharbor.settings")
        import django
        django.setup()
        import pika
        from django.conf import settings
        from vidharbor.models import Outbox

        expected = {}
        for item in json.loads(sys.argv[1]):
            entry = Outbox.objects.select_related("job__video").get(pk=item["message_id"])
            assert str(entry.job_id) == item["job_id"]
            assert str(entry.job.video_id) == item["video_id"]
            assert str(entry.job.video.upload_id) == item["upload_id"]
            assert entry.kind == "video.transcode.v1" and entry.attempt_no == 1
            assert entry.payload == {
                "version": 1, "message_id": item["message_id"],
                "job_id": item["job_id"], "video_id": item["video_id"],
                "generation": entry.job.video.generation, "next_attempt_no": 1,
                "request_id": item["upload_id"], "trace_context": {},
            }
            expected[item["message_id"]] = entry.payload

        connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=settings.RABBITMQ_HOST,
            credentials=pika.PlainCredentials(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD),
            heartbeat=30, socket_timeout=3, stack_timeout=5, blocked_connection_timeout=5,
        ))
        channel = connection.channel()
        last_tag = None
        observed = set()
        deadline = time.monotonic() + 20
        try:
            queue = channel.queue_declare(queue="video.jobs", passive=True)
            assert queue.method.consumer_count == 0, "Use an isolated test stack without workers"
            inspected = 0
            while time.monotonic() < deadline and observed != set(expected):
                method, properties, body = channel.basic_get(queue="video.jobs", auto_ack=False)
                if method is None:
                    connection.process_data_events(time_limit=0.2)
                    continue
                last_tag = method.delivery_tag
                inspected += 1
                assert inspected <= 1000, "Too many unrelated messages; use a fresh test stack"
                if properties.message_id in expected:
                    assert method.routing_key == "video.transcode.v1"
                    assert properties.delivery_mode == 2
                    assert properties.content_type == "application/json"
                    assert json.loads(body) == expected[properties.message_id]
                    observed.add(properties.message_id)
            assert observed == set(expected), {"missing_message_ids": sorted(set(expected) - observed)}
            while Outbox.objects.filter(pk__in=expected, sent_at__isnull=False).count() != len(expected):
                assert time.monotonic() < deadline, "Relay did not confirm delivery in outbox"
                connection.process_data_events(time_limit=0.2)
            print(json.dumps({"delivered_message_ids": sorted(observed)}))
        finally:
            try:
                if last_tag is not None and channel.is_open:
                    channel.basic_nack(delivery_tag=last_tag, multiple=True, requeue=True)
            finally:
                connection.close()
    ''')
    result = json.loads(exec_service("api", "python", "-c", code, json.dumps(uploads)))
    expected = sorted(item["message_id"] for item in uploads)
    assert result == {"delivered_message_ids": expected}, result
    print(f"RabbitMQ: original IDs and full payloads verified, persistent messages requeued: {expected}")


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
            check_delivery([
                {**result_a, "upload_id": id_a},
                {**result_b, "upload_id": id_b},
            ])
            page_a.get_by_role("button", name="Выйти").click()
            expect(page_a.get_by_role("heading", name="Вход")).to_be_visible()
            assert context_a.request.get(BASE_URL + "/api/videos").status == 401
            print(f"Browser progress: author A {progress_a}; author B {progress_b}")
            print("Two sessions, owner checks, CSRF, abort and new upload, lost reply and identical IDs on retry: passed")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
