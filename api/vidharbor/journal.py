"""Fsync-backed authority kept outside PostgreSQL and excluded from database rollback.

The carrier's independence/retention are operator decisions. A filesystem on the
same host is sufficient for an isolated restore drill, not a host-loss backup.
"""
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path

from django.conf import settings

from .storage import sync_directory


class JournalUnavailable(OSError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


@contextmanager
def locked():
    root = Path(settings.JOURNAL_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "lock").open("ab") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield root


def _read(root):
    path = root / "ledger.jsonl"
    entries, previous = [], "0" * 64
    try:
        for line in path.read_bytes().splitlines() if path.exists() else []:
            entry = json.loads(line)
            digest = entry.pop("digest")
            if (entry["sequence"] != len(entries) + 1 or entry["previous"] != previous
                    or hashlib.sha256(canonical(entry)).hexdigest() != digest):
                raise ValueError("invalid journal chain")
            entry["digest"] = digest
            entries.append(entry)
            previous = digest
        head = {"sequence": len(entries), "digest": previous}
        marker = root / "head.json"
        if (marker.exists() and json.loads(marker.read_bytes()) != head) or (entries and not marker.exists()):
            raise ValueError("journal authority watermark mismatch")
        return entries, head
    except (ValueError, KeyError, TypeError) as exc:
        raise JournalUnavailable("Journal is incomplete or damaged; access stays closed") from exc


def read_entries():
    with locked() as root:
        return _read(root)


def append(kind, key, data):
    """Idempotent durable reservation; a failed DB transaction never releases it."""
    with locked() as root:
        entries, head = _read(root)
        found = next((e for e in entries if e["kind"] == kind and e["key"] == str(key)), None)
        if found:
            if found["data"] != data:
                raise JournalUnavailable("Journal key already has different data")
            return found
        entry = {"sequence": head["sequence"] + 1, "previous": head["digest"],
                 "kind": kind, "key": str(key), "data": data}
        entry["digest"] = hashlib.sha256(canonical(entry)).hexdigest()
        with (root / "ledger.jsonl").open("ab") as stream:
            stream.write(canonical(entry) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary = root / "head.tmp"
        with temporary.open("wb") as stream:
            stream.write(canonical({"sequence": entry["sequence"], "digest": entry["digest"]}))
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(root / "head.json")
        sync_directory(root)
        return entry


def deletion_record(video_id):
    entries, _ = read_entries()
    return next((e for e in entries if e["kind"] == "delete" and e["key"] == str(video_id)), None)


def is_deleted(video_id):
    return deletion_record(video_id) is not None
