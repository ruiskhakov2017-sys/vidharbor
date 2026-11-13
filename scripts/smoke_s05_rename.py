"""Check S05 rename in a running disposable Compose project; leaves labelled fixtures."""

import os
import secrets
import shutil
import subprocess
import uuid

from playwright.sync_api import expect, sync_playwright

from smoke_s02 import BASE_URL, COMPOSE, register_and_sign_in


def main():
    expect.set_options(timeout=45000)
    suffix = uuid.uuid4().hex[:12]
    owner, other = f"s05_owner_{suffix}", f"s05_other_{suffix}"
    password = secrets.token_urlsafe(24)
    chrome = os.getenv("VIDHARBOR_CHROME") or shutil.which("chromium") or shutil.which("google-chrome")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, args=["--no-sandbox"])
        owner_context = browser.new_context()
        page = owner_context.new_page()
        register_and_sign_in(page, owner, password)
        # S04 publication is not available yet. Seed a QUEUED video to exercise real rename/list APIs.
        code = (
            "from django.contrib.auth import get_user_model; "
            "from vidharbor.models import Upload, Video; "
            f"owner = get_user_model().objects.get(username={owner!r}); "
            "upload = Upload.objects.create(owner=owner, title='S05 fixture', expected_size=1, "
            "state=Upload.State.COMPLETE); "
            "video = Video.objects.create(owner=owner, upload=upload, title='S05 fixture'); "
            "print(video.id)"
        )
        video_id = subprocess.check_output(
            [*COMPOSE, "api", "python", "manage.py", "shell", "-c", code], text=True, timeout=120,
        ).strip().splitlines()[-1]
        uuid.UUID(video_id)
        page.reload()
        row = page.get_by_role("listitem").filter(has_text="S05 fixture")
        row.get_by_role("button", name="Переименовать", exact=True).click()
        page.get_by_label("Новое название", exact=True).fill("Не сохранять")
        page.get_by_role("button", name="Отмена", exact=True).click()
        expect(row.locator("strong")).to_have_text("S05 fixture")
        row.get_by_role("button", name="Переименовать", exact=True).click()
        title = '<img src=x onerror="window.s05Injected=true">'
        page.get_by_label("Новое название", exact=True).fill(title)
        route_pattern = "**/api/videos/*/rename"
        page.route(route_pattern, lambda route: route.fulfill(
            status=503, content_type="application/json", body='{"error":"Тестовый отказ"}',
        ), times=1)
        page.get_by_role("button", name="Сохранить", exact=True).click()
        expect(page.get_by_role("alert")).to_have_text("Тестовый отказ")
        expect(page.get_by_label("Новое название", exact=True)).to_have_value(title)
        page.get_by_role("button", name="Сохранить", exact=True).click()
        expect(page.get_by_role("status")).to_have_text("Название сохранено")
        expect(page.locator(".videos strong")).to_have_text(title)
        page.reload()
        expect(page.locator(".videos strong")).to_have_text(title)
        assert not page.evaluate("Boolean(window.s05Injected)")
        expect(page.locator(".videos img")).to_have_count(0)

        other_context = browser.new_context()
        other_page = other_context.new_page()
        register_and_sign_in(other_page, other, password)
        expect(other_page.get_by_role("listitem")).to_have_count(0)
        path = f"/api/videos/{video_id}/rename"
        token = other_context.request.get(BASE_URL + "/api/auth/session").json()["csrf_token"]
        denied = other_context.request.post(
            BASE_URL + path, data={"title": "Чужое"}, headers={"X-CSRFToken": token},
        )
        assert denied.status == 404, denied.status
        assert owner_context.request.post(BASE_URL + path, data={"title": "Без CSRF"}).status == 403
        page.reload()
        expect(page.locator(".videos strong")).to_have_text(title)
        browser.close()
    print("S05 rename: save, reload, cancel, error retry, HTML escaping, owner isolation and CSRF passed.")
    print("QUEUED fixture only; catalogue, search, playback and deletion require S04.")


if __name__ == "__main__":
    main()
