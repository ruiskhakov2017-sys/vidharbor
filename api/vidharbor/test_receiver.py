"""Isolated HTTP test receiver. PostgreSQL emulates fixed sheet rows, not Google API."""
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from django.db import close_old_connections, transaction

from .models import ReceiverRow
from .notifications import signature, validate_payload


def write_row(payload):
    number = payload["sheet_row"]
    # Unique PK resolves concurrent creation; lock covers read/check/write, even for
    # DIFFERENT event IDs. A real n8n workflow needs the same serialization boundary.
    with transaction.atomic():
        ReceiverRow.objects.get_or_create(number=number)
        row = ReceiverRow.objects.select_for_update().get(pk=number)
        if row.event_id is not None and str(row.event_id) != payload["event_id"]:
            return 409
        if row.payload is not None and row.payload != payload:
            return 409
        row.event_id, row.payload = payload["event_id"], payload
        row.save()
    return 200


def receiver(secret, address=("127.0.0.1", 8096)):
    if len(secret) < 32:
        raise ValueError("webhook_secret_too_short")

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # never log credentials, raw body, or URL

        def do_POST(self):
            close_old_connections()
            self.connection.settimeout(5)
            try:
                self.receive()
            finally:
                close_old_connections()

        def reply(self, status, payload):
            raw = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def receive(self):
            if self.path != "/video-ready":
                return self.reply(404, {"status": "not_found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                stamp = self.headers.get("X-VidHarbor-Timestamp", "")
                if not 0 < size <= 16384:
                    return self.reply(400, {"status": "invalid_request"})
                if abs(time.time() - int(stamp)) > 300:
                    return self.reply(403, {"status": "invalid_signature"})
                body = self.rfile.read(size)
                supplied = self.headers.get("X-VidHarbor-Signature", "")
                if not hmac.compare_digest(signature(secret, stamp, body), supplied):
                    return self.reply(403, {"status": "invalid_signature"})
                payload = json.loads(body)
                validate_payload(payload)
            except (ValueError, TypeError, AttributeError, UnicodeError, OverflowError):
                return self.reply(400, {"status": "invalid_request"})
            status = write_row(payload)
            self.reply(status, {"event_id": payload["event_id"],
                                "status": "written" if status == 200 else "conflict"})

    return ThreadingHTTPServer(address, Handler)

