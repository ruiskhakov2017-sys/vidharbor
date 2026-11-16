import json
import os
import shutil
import subprocess
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

BASE_URL = f"http://127.0.0.1:{os.getenv('VIDHARBOR_PORT', '8085')}"
COMPOSE = ["docker", "compose", "--env-file", ".env.example"]


def request_json(path):
    try:
        with urlopen(BASE_URL + path, timeout=8) as response:
            return response.status, json.load(response)
    except HTTPError as response:
        with response:
            return response.code, json.load(response)


def check_health(label, *, postgres=True, rabbitmq=True):
    available = postgres and rabbitmq
    expected_ready = (
        200 if available else 503,
        {
            "status": "ready" if available else "not_ready",
            "checks": {"postgres": postgres, "rabbitmq": rabbitmq},
        },
    )
    expected_live = (200, {"status": "live"})
    deadline = time.monotonic() + 70
    while True:
        try:
            live = request_json("/health/live")
            ready = request_json("/health/ready")
            if live == expected_live and ready == expected_ready:
                print(f"{label}: live={live[0]}, ready={ready[0]} {ready[1]['checks']}", flush=True)
                return
            observed = f"live={live!r}, ready={ready!r}"
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            observed = str(error)

        if time.monotonic() >= deadline:
            raise RuntimeError(f"{label}: unexpected health responses: {observed}")
        time.sleep(1)


def check_page():
    from playwright.sync_api import expect, sync_playwright

    chrome = os.getenv("VIDHARBOR_CHROME") or shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Install Chrome/Chromium or set VIDHARBOR_CHROME")
    # Use an isolated automation context and wait for Vue itself. CLI --dump-dom
    # depends on browser startup/session behaviour and can hang after rendering.
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=chrome, args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        try:
            page = browser.new_page()
            response = page.goto(BASE_URL + "/", wait_until="domcontentloaded", timeout=30000)
            if response is None or response.status != 200:
                raise RuntimeError("The sign-in page did not return HTTP 200")
            expect(page.get_by_test_id("auth-screen")).to_be_visible(timeout=10000)
            print(f"Chrome {browser.version}: HTTP 200 and visible Vue auth-screen", flush=True)
        finally:
            browser.close()
    print("Chrome rendered the Vue sign-in screen.", flush=True)


def main():
    check_health("Clean start")
    check_page()

    for service, checks in (("rabbitmq", {"rabbitmq": False}), ("db", {"postgres": False})):
        subprocess.run([*COMPOSE, "stop", "--timeout", "8", service], check=True, timeout=30)
        try:
            check_health(f"{service} stopped", **checks)
        finally:
            subprocess.run([*COMPOSE, "start", service], check=True, timeout=45)
        check_health(f"{service} restarted")


if __name__ == "__main__":
    main()
