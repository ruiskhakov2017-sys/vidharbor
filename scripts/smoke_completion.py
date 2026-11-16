"""Ordinary browser upload -> real queue/FFmpeg -> public HLS, with resource evidence."""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import expect, sync_playwright
from smoke_s04 import BASE, login_via_ui, require, verify_playback

COMPOSE = ["docker", "compose", "--env-file", ".env.example"]


def database(code):
    raw = subprocess.check_output([*COMPOSE, "exec", "-T", "upload", "python", "manage.py", "shell", "-c", code], text=True)
    return json.loads(raw.splitlines()[-1])


def bytes_value(text):
    value, unit = re.match(r"([0-9.]+)([A-Za-z]+)", text.strip()).groups()
    return float(value) * {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3,
                          "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}[unit]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--large", action="store_true")
    args = parser.parse_args()
    assert os.environ.get("COMPOSE_PROJECT_NAME", "").startswith("vidharbor-"), "Dedicated test project required"
    source = args.source.resolve()
    if args.large:
        assert 3_000_000_000 <= source.stat().st_size <= 5 * 1024**3
        assert source.stat().st_blocks * 512 >= source.stat().st_size, "Sparse/padded placeholders are not a media test"
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    report = {"source": str(source), "bytes": source.stat().st_size, "sha256": digest,
              "large": args.large, "status": "RUNNING", "peaks": {}, "disk_free_before": shutil.disk_usage(source.parent).free}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    stopped = threading.Event()
    containers = [os.environ["COMPOSE_PROJECT_NAME"] + "-" + service + "-1" for service in ("api", "upload", "worker")]
    probe = '''import os,json
from pathlib import Path
rows=[]
for p in Path('/proc').iterdir():
 if p.name.isdigit() and int(p.name)!=os.getpid():
  try:
   s=(p/'stat').read_text().split(') ',1)[1].split()
   rows.append({'pid':int(p.name),'rss':int((p/'statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE'),'ticks':int(s[11])+int(s[12])})
  except (OSError,ValueError,IndexError):pass
print(json.dumps({'hz':os.sysconf('SC_CLK_TCK'),'rows':rows}))'''
    report["resource_measurement"] = "PID-namespace process RSS sum (shared pages may be counted twice) and sampled CPU ticks; Docker cgroup=none, configured limits not enforced"
    report["catalog"] = {"samples": 0, "max_seconds": 0, "statuses": []}
    previous = {}
    def monitor():
        while not stopped.is_set():
            for container in containers:
                result = subprocess.run(["docker", "exec", container, "python", "-c", probe], capture_output=True, text=True)
                if result.returncode: continue
                value = json.loads(result.stdout); now = time.monotonic()
                peak = report["peaks"].setdefault(container, {"memory_bytes": 0, "cpu_percent": 0})
                peak["memory_bytes"] = max(peak["memory_bytes"], sum(p["rss"] for p in value["rows"]))
                if container in previous:
                    at, old = previous[container]
                    ticks = sum(max(0, p["ticks"] - old[p["pid"]]) for p in value["rows"] if p["pid"] in old)
                    peak["cpu_percent"] = max(peak["cpu_percent"], ticks / value["hz"] / (now-at) * 100)
                previous[container] = (now, {p["pid"]:p["ticks"] for p in value["rows"]})
            at = time.monotonic()
            try:
                with urlopen(BASE + "/api/catalog", timeout=5) as response: status = response.status
            except Exception: status = 0
            report["catalog"]["samples"] += 1
            report["catalog"]["statuses"].append(status)
            report["catalog"]["max_seconds"] = max(report["catalog"]["max_seconds"], time.monotonic()-at)
            report["disk_free_minimum"] = min(report.get("disk_free_minimum", report["disk_free_before"]), shutil.disk_usage(source.parent).free)
            stopped.wait(1)
    monitor_thread = threading.Thread(target=monitor, daemon=True); monitor_thread.start()
    started = time.monotonic()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=os.getenv("VIDHARBOR_CHROME", "/usr/bin/chromium"),
                args=["--no-sandbox", "--disable-dev-shm-usage"])
            report["browser"] = browser.version
            page = browser.new_page(); expect.set_options(timeout=45000)
            suffix = uuid.uuid4().hex[:10]
            login_via_ui(page, "accept-" + suffix, uuid.uuid4().hex)
            title = ("Большой синтетический " if args.large else "Сквозное демо ") + suffix
            page.get_by_label("Название видео").fill(title)
            page.get_by_label("Видеофайл").set_input_files(source)
            page.evaluate("""() => {
                window.uploadProgress = [];
                new MutationObserver(() => {
                    const p = document.querySelector('progress');
                    if (p && !window.uploadProgress.includes(p.value)) window.uploadProgress.push(p.value);
                }).observe(document.body, {childList:true,subtree:true,attributes:true});
            }""")
            upload_started = time.monotonic()
            with page.expect_response(lambda response: response.url.endswith("/complete"), timeout=600000) as completed:
                page.get_by_role("button", name="Загрузить", exact=True).click()
            response = completed.value; require(response)
            report["upload"] = response.json()
            page.context.storage_state(path=str(args.report.with_suffix(".session.json")))
            args.report.with_suffix(".session.json").chmod(0o600)
            report["upload_seconds"] = time.monotonic() - upload_started
            report["progress"] = page.evaluate("window.uploadProgress")
            print(json.dumps({"upload_complete": report["upload"], "seconds": report["upload_seconds"]}), flush=True)
            video_id = report["upload"]["video_id"]
            ready_started = time.monotonic()
            expect(page.get_by_role("link", name="Готово · Смотреть", exact=True)).to_be_visible(timeout=600000)
            report["processing_seconds_after_upload"] = time.monotonic() - ready_started
            report["original"] = database(
                "import json; from vidharbor.models import Video,Job,Outbox; from vidharbor.storage import original_path,file_digest; "
                f"v=Video.objects.get(pk={video_id!r}); p=original_path(v.upload_id); "
                "print(json.dumps({'sha256':file_digest(p),'bytes':p.stat().st_size,'inode':p.stat().st_ino,"
                "'generation':v.generation,'state':v.state,'jobs':Job.objects.filter(video=v).count(),"
                "'ready_events':Outbox.objects.filter(job__video=v,kind='video.ready').count()}))")
            assert report["original"]["sha256"] == digest
            assert report["original"]["bytes"] == source.stat().st_size
            assert report["original"]["jobs"] == report["original"]["ready_events"] == 1
            guest = browser.new_context(); guest_page = guest.new_page()
            detail = require(guest.request.get(f"{BASE}/api/catalog/{video_id}")).json()
            master = require(guest.request.get(BASE + detail["playlist_url"]))
            sizes = [tuple(map(int, pair)) for pair in re.findall(r"RESOLUTION=(\d+)x(\d+)", master.text())]
            verify_playback(guest_page, detail, sizes)
            report["qualities"] = sizes
            report["watch_url"] = BASE + detail["watch_url"]
            report["master_url"] = BASE + detail["playlist_url"]
            guest_page.screenshot(path=str(args.report.with_suffix(".png")), full_page=True)
            report["playback"] = "PASS: public Auto and each manual quality"
            if args.large:
                assert any(0 < value < 100 for value in report["progress"]), report["progress"]
                assert report["catalog"]["samples"] and set(report["catalog"]["statuses"]) == {200}
            browser.close()
        report["status"] = "PASS"
    except Exception as error:
        report["status"] = "FAIL"; report["error"] = repr(error)
        raise
    finally:
        stopped.set(); monitor_thread.join(timeout=10)
        report["total_seconds"] = time.monotonic() - started
        report["disk_free_after"] = shutil.disk_usage(source.parent).free
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
