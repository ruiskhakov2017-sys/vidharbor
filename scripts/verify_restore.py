"""Post-restore functional evidence: old URLs, original, queue, DLQ and allocator."""
import argparse
import json
import os
import time
import uuid
from pathlib import Path

from playwright.sync_api import sync_playwright
import smoke_s04
from s08 import Stand


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--large-report", type=Path, required=True)
    parser.add_argument("--deleted-report", type=Path, required=True)
    parser.add_argument("--pending-report", type=Path, required=True)
    parser.add_argument("--dlq-report", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(); config = json.loads(args.config.read_text())
    stand = Stand(config["target"], Path(config["reports"]))
    large = json.loads(args.large_report.read_text()); deleted = json.loads(args.deleted_report.read_text())
    pending = json.loads(args.pending_report.read_text()); dlq = json.loads(args.dlq_report.read_text().splitlines()[-1])
    for value in (large["upload"]["video_id"], pending["video_id"], deleted["video_id"]): uuid.UUID(value)
    def database(code):
        raw = stand.run(["exec", "-T", "upload", "python", "manage.py", "shell", "-c", code], label="functional-db")
        return json.loads(raw.decode().splitlines()[-1])
    origin = config["target"]["env"]["VIDHARBOR_PUBLIC_BASE_URL"]
    smoke_s04.BASE = origin
    result = {"status":"RUNNING", "origin":origin, "target_project":stand.project}
    try:
        with sync_playwright() as playwright:
            request = playwright.request.new_context()
            for path in deleted["closed_links"]:
                smoke_s04.require(request.get(origin + path), 404)
            smoke_s04.require(request.get(large["master_url"]))
            deadline = time.monotonic() + 150
            while True:
                response = request.get(origin + "/api/catalog/" + pending["video_id"])
                if response.status == 200: break
                assert response.status == 404 and time.monotonic() < deadline
                time.sleep(.5)
            result["pending_job"] = database("import json; from vidharbor.models import Job,Outbox; "
                f"j=Job.objects.get(pk={pending['job_id']!r}); print(json.dumps({{'id':str(j.id),'state':j.state,'generation':j.generation,"
                "'attempts':list(j.attempts.values_list('number','state')),'ready_events':Outbox.objects.filter(job=j,kind='video.ready').count()}))")
            assert result["pending_job"]["state"] == "SUCCEEDED"
            assert result["pending_job"]["generation"] == result["pending_job"]["ready_events"] == 1
            assert result["pending_job"]["attempts"] == [[1, "SUCCEEDED"]]
            result["original"] = database("import json; from vidharbor.models import Video; from vidharbor.storage import original_path,file_digest; "
                f"v=Video.objects.get(pk={large['upload']['video_id']!r}); p=original_path(v.upload_id); "
                "print(json.dumps({'sha256':file_digest(p),'bytes':p.stat().st_size,'inode':p.stat().st_ino,'state':v.state}))")
            assert result["original"]["sha256"] == large["sha256"]
            assert result["original"]["bytes"] == large["bytes"]
            result["deleted"] = database("import json; from vidharbor.models import Video; from vidharbor.storage import original_path; "
                f"v=Video.objects.get(pk={deleted['video_id']!r}); print(json.dumps({{'state':v.state,'original_exists':original_path(v.upload_id).exists()}}))")
            assert result["deleted"] == {"state":"DELETED","original_exists":False}
            result["browsers"] = []
            for name in ("Chrome", "Edge", "Firefox"):
                browser = smoke_s04.launch_browser(playwright, name)
                page = browser.new_page(); detail = smoke_s04.require(page.request.get(origin + "/api/catalog/" + large["upload"]["video_id"])).json()
                smoke_s04.verify_playback(page, detail, [tuple(size) for size in large["qualities"]])
                result["browsers"].append({"name":name,"version":browser.version,"Auto_and_manual":"PASS"})
                page.screenshot(path=str(args.report.with_name(args.report.stem+"-"+name.lower()+".png")))
                print(json.dumps(result["browsers"][-1]),flush=True);browser.close()
            request.dispose()
        rows = database("import json,hashlib; from vidharbor.management.commands import relay_outbox as r; c,ch=r.connect(); rows=[]; tags=[]\n"
            "try:\n"
            " for _ in range(1000):\n"
            "  method,props,body=ch.basic_get(r.ERROR_QUEUE,auto_ack=False)\n"
            "  if method is None:break\n"
            "  tags.append(method.delivery_tag); rows.append({'message_id':props.message_id,'sha256':hashlib.sha256(body).hexdigest()})\n"
            "finally:\n"
            " for tag in tags:ch.basic_nack(tag,requeue=True)\n"
            " c.close()\n"
            "print(json.dumps(rows))")
        assert dlq in rows;result["raw_dlq_preserved"] = dlq
        allocation = database("import json,threading,uuid; from vidharbor.models import ReadyEvent,SheetAllocator,ReceiverRow; "
            "from vidharbor.notifications import attach_existing_ready,Webhook,claim,finish; from vidharbor.test_receiver import receiver; "
            "attach_existing_ready(); "
            f"e=ReadyEvent.objects.get(video_id={pending['video_id']!r}); "
            "secret=b'restore-test-only-'+uuid.uuid4().hex.encode(); server=receiver(secret,('127.0.0.1',0)); "
            "t=threading.Thread(target=server.serve_forever,daemon=True); t.start(); "
            "w=Webhook(f'http://127.0.0.1:{server.server_port}/video-ready',['127.0.0.1'],secret,allow_test_loopback=True); "
            "sending=claim(e.pk); outcome,reason,delay=w.send(e); finish(sending,outcome,reason,delay); assert outcome=='written'; "
            "assert w.send(e)[0]=='written'; server.shutdown(); server.server_close(); t.join(); "
            "print(json.dumps({'event_id':str(e.pk),'row':e.sheet_row,'allocator':SheetAllocator.objects.get(pk=1).next_row,"
            "'records_for_event':ReceiverRow.objects.filter(event_id=e.pk).count()}))")
        assert allocation["row"] >= 4 and allocation["allocator"] > allocation["row"] and allocation["records_for_event"] == 1
        result.update(status="PASS", old_absolute_urls="preserved through local fenced cutover", allocation=allocation,
                      real_n8n_sheets="Not Run")
    except Exception as exc:
        result.update(status="FAIL",error=repr(exc));raise
    finally:
        args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
        print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__ == "__main__": main()
