import json
import os
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
    browser = subprocess.run(
        [
            "google-chrome", "--headless", "--no-sandbox", "--disable-dev-shm-usage",
            "--disable-background-networking", "--dump-dom", BASE_URL + "/",
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    if 'data-testid="s01-placeholder"' not in browser.stdout:
        raise RuntimeError("Chrome did not render the Vue placeholder")
    print("Chrome rendered the Vue placeholder.", flush=True)


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
