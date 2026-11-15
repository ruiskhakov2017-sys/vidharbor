import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import timezone as utc_timezone
from functools import partial

from django.conf import settings
from django.utils import timezone

from .job_attempts import execute_attempt, finish_attempt
from .models import Attempt, Job, Outbox, Video
from .storage import file_digest, original_path, sync_directory
from .transcode import encode, filesystem_failure


logger = logging.getLogger(__name__)
MANIFEST = "manifest.json"
READY_EVENT = "video.ready"
VARIANT = r"v[0-2]/index\.m3u8"
SEGMENT = r"seg-[0-9]{6,}\.ts"


class InvalidHLS(ValueError):
    pass


def staging_path(video_id):
    return settings.MEDIA_ROOT / "processing" / str(video_id)


def ready_path(video_id, generation):
    return settings.MEDIA_ROOT / "ready" / str(video_id) / f"g{generation}"


def master_url(video_id):
    return f"/media/{video_id}/master.m3u8"


def playlist_lines(path):
    if path.is_symlink() or not path.is_file():
        raise InvalidHLS(f"Missing playlist: {path.name}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "#EXTM3U" or any("URI=" in line for line in lines):
        raise InvalidHLS("Unexpected playlist format")
    return lines


def variant_paths(lines, prefix=""):
    variants = [line for line in lines if line and not line.startswith("#")]
    if not 1 <= len(variants) <= 3 or len(set(variants)) != len(variants):
        raise InvalidHLS("Unexpected number of variants")
    if len(variants) != sum(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
        raise InvalidHLS("Missing variant attributes")
    if any(not re.fullmatch(re.escape(prefix) + VARIANT, name) for name in variants):
        raise InvalidHLS("Invalid variant path")
    return variants


def scope_master(directory, generation):
    path = directory / "master.m3u8"
    lines = playlist_lines(path)
    variants = set(variant_paths(lines))
    path.write_text(
        "".join(f"g{generation}/{line}\n" if line in variants else f"{line}\n" for line in lines),
        encoding="utf-8",
    )


def hls_files(directory, generation):
    if directory.is_symlink():
        raise InvalidHLS("Invalid HLS directory")
    master = directory / "master.m3u8"
    prefix = f"g{generation}/"
    variants = variant_paths(playlist_lines(master), prefix)
    files = {master}
    for relative in variants:
        playlist = directory / relative.removeprefix(prefix)
        if playlist.parent.is_symlink():
            raise InvalidHLS("Invalid variant directory")
        lines = playlist_lines(playlist)
        segments = [line for line in lines if line and not line.startswith("#")]
        if "#EXT-X-ENDLIST" not in lines or not segments:
            raise InvalidHLS("Incomplete variant")
        if len(segments) != sum(line.startswith("#EXTINF:") for line in lines):
            raise InvalidHLS("Invalid segment list")
        files.add(playlist)
        for segment in segments:
            path = playlist.parent / segment
            if not re.fullmatch(SEGMENT, segment) or path.is_symlink() or not path.is_file() or not path.stat().st_size:
                raise InvalidHLS("Missing or invalid segment")
            files.add(path)
    present = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise InvalidHLS("Unexpected link in HLS output")
        if path.is_file() and path != directory / MANIFEST:
            present.add(path)
    if files != present:
        raise InvalidHLS("Unexpected file in HLS output")
    return sorted(files)


def metadata(job, attempt_id):
    return {
        "version": 1, "video_id": str(job.video_id), "job_id": str(job.id),
        "generation": job.generation, "attempt_id": attempt_id,
    }


def write_manifest(directory, job, attempt_id):
    hashes = {}
    folders = set()
    for path in hls_files(directory, job.generation):
        with path.open("rb") as stream:
            hashes[path.relative_to(directory).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
            os.fsync(stream.fileno())
        folders.add(path.parent)
    with (directory / MANIFEST).open("x", encoding="utf-8") as stream:
        json.dump({**metadata(job, attempt_id), "files": hashes}, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    for folder in sorted(folders - {directory}):
        sync_directory(folder)
    sync_directory(directory)
    sync_directory(directory.parent)


def verify_manifest(directory, job, attempt_id):
    path = directory / MANIFEST
    if path.is_symlink() or not path.is_file():
        raise InvalidHLS("Missing manifest")
    expected = {
        path.relative_to(directory).as_posix(): file_digest(path)
        for path in hls_files(directory, job.generation)
    }
    if json.loads(path.read_text(encoding="utf-8")) != {**metadata(job, attempt_id), "files": expected}:
        raise InvalidHLS("Manifest mismatch")


def publish_video(job, *, recovered=False):
    # Called by finish_attempt within the transaction that also completes the job.
    video = Video.objects.select_for_update().get(pk=job.video_id)
    from .journal import is_deleted
    if is_deleted(video.id):
        return False
    if video.generation != job.generation or video.state != Video.State.QUEUED:
        return False
    destination = ready_path(video.id, job.generation)
    if not recovered:
        source = staging_path(video.id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() or destination.is_symlink():
            raise FileExistsError(destination)
        source.rename(destination)
        for folder in (source.parent, destination.parent, destination.parent.parent, settings.MEDIA_ROOT):
            sync_directory(folder)
    ready_at = timezone.now()
    video.state = Video.State.READY
    video.published_generation = job.generation
    video.ready_at = ready_at
    video.save(update_fields=["state", "published_generation", "ready_at"])
    event_id = uuid.uuid4()
    Outbox.objects.create(
        message_id=event_id, job=job, kind=READY_EVENT, attempt_no=0,
        payload={
            "type": READY_EVENT, "version": 1, "event_id": str(event_id), "video_id": str(video.id),
            "title": video.title,
            "ready_at": ready_at.astimezone(utc_timezone.utc).isoformat().replace("+00:00", "Z"),
            "playlist_url": settings.PUBLIC_BASE_URL + master_url(video.id),
        },
    )
    return True


def recover_publication(job, payload):
    if job.state != Job.State.PROCESSING:
        return False
    if not Video.objects.filter(pk=job.video_id, generation=job.generation, state=Video.State.QUEUED).exists():
        return False
    directory = ready_path(job.video_id, job.generation)
    if not directory.exists() and not directory.is_symlink():
        return False
    attempt = job.attempts.filter(state=Attempt.State.RUNNING).order_by("-number").first()
    if attempt is None:
        return False
    try:
        verify_manifest(directory, job, attempt.pk)
    except (InvalidHLS, ValueError):
        logger.warning("Discarding unpublished invalid HLS for job %s", job.id)
        if directory.is_symlink() or not directory.is_dir():
            directory.unlink()
        else:
            shutil.rmtree(directory)
        return False
    finish_attempt(attempt.pk, "success", payload, publish=partial(publish_video, recovered=True))
    logger.info("Recovered publication for job %s without rerunning FFmpeg", job.id)
    return True


def transcode(job, attempt_id, lock_fd, *, cancelled):
    directory = staging_path(job.video_id)
    try:
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
        outcome, reason = encode(
            original_path(job.video.upload_id), directory, lock_fd, cancelled, timeout=settings.FFMPEG_TIMEOUT,
        )
        if outcome != "success":
            shutil.rmtree(directory)
            return outcome, reason
        scope_master(directory, job.generation)
        write_manifest(directory, job, attempt_id)
    except (InvalidHLS, UnicodeError):
        logger.warning("FFmpeg produced invalid HLS for job %s", job.id, exc_info=True)
        return "failed", "invalid_hls"
    except OSError as exc:
        logger.warning("HLS filesystem error for job %s", job.id, exc_info=True)
        return filesystem_failure(exc)
    return "success", ""


def process_delivery(job, payload, lock_fd, cancelled):
    from .deletion import Revocation
    cancelled = Revocation(cancelled, job.video_id, payload["generation"])
    if cancelled.is_set():
        return
    job.refresh_from_db()
    if job.generation == payload["generation"] and recover_publication(job, payload):
        return
    execute_attempt(
        job, payload, lock_fd, cancelled, partial(transcode, cancelled=cancelled), publish=publish_video,
    )
