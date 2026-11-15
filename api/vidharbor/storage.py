import fcntl
import hashlib
import os
import time
from contextlib import contextmanager

from django.conf import settings


CHUNK_SIZE = 256 * 1024
DISK_RESERVE = 64 * 1024 * 1024


def part_path(upload_id):
    return settings.MEDIA_ROOT / "uploads" / f"{upload_id}.part"


def original_path(upload_id):
    return settings.MEDIA_ROOT / "original" / str(upload_id)


@contextmanager
def media_lock(name, *, timeout=0):
    path = settings.MEDIA_ROOT / "locks" / str(name)
    with path.open("ab") as handle:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    yield False
                    return
                time.sleep(min(0.05, remaining))
            else:
                yield True
                return


def file_digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
