import errno
import logging
import os
import signal
import subprocess
import tempfile
import time
from . import telemetry
from fractions import Fraction

from .media_probe import InvalidVideo, ProbeTimeout, probe_video


logger = logging.getLogger(__name__)
LADDER = (
    (640, 360, "850k"),
    (854, 480, "1300k"),
    (1280, 720, "2600k"),
)
SEGMENT_SECONDS = 2


def select_streams(streams):
    videos = [
        stream for stream in streams
        if stream["codec_type"] == "video" and stream.get("codec_name")
        and stream.get("width", 0) > 0 and stream.get("height", 0) > 0
        and not stream.get("disposition", {}).get("attached_pic")
    ]
    audio = [stream for stream in streams if stream["codec_type"] == "audio"]

    def is_default(stream):
        return bool(stream.get("disposition", {}).get("default"))

    return max(videos, key=is_default), max(audio, key=is_default, default=None)


def rendition_sizes(video):
    width, height = Fraction(video["width"]), Fraction(video["height"])
    sar = video.get("sample_aspect_ratio", "1:1")
    if sar not in ("0:1", "N/A"):
        try:
            width *= Fraction(sar.replace(":", "/"))
        except (ValueError, ZeroDivisionError) as exc:
            raise InvalidVideo("Некорректные пропорции кадра.") from exc
    angle = next(
        (data["rotation"] for data in video.get("side_data_list", []) if "rotation" in data),
        video.get("tags", {}).get("rotate", 0),
    )
    try:
        rotation = int(angle) % 360
    except (TypeError, ValueError) as exc:
        raise InvalidVideo("Некорректный поворот кадра.") from exc
    if rotation not in (0, 90, 180, 270):
        raise InvalidVideo("Поддерживается поворот кадра на 90, 180 или 270 градусов.")
    if rotation in (90, 270):
        width, height = height, width
    if width < 2 or height < 2:
        raise InvalidVideo("Недопустимый размер кадра.")

    sizes = []
    for horizontal_width, horizontal_height, bitrate in LADDER:
        limit_width, limit_height = (
            (horizontal_height, horizontal_width) if height > width else (horizontal_width, horizontal_height)
        )
        scale = min(limit_width / width, limit_height / height)
        if scale <= 1:
            dimensions = (max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2))
            if not sizes or dimensions != sizes[-1][:2]:
                sizes.append((*dimensions, bitrate))
    return sizes or [(int(width) // 2 * 2, int(height) // 2 * 2, LADDER[0][2])]


def command_for(source, video, audio, sizes):
    cmd = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-xerror",
        "-protocol_whitelist", "file", "-format_whitelist", "mov,matroska,webm",
        "-max_streams", "64", "-threads", "2", "-i", str(source),
        "-filter_complex_threads", "1",
    ]
    filters = []
    if len(sizes) > 1:
        labels = "".join(f"[in{index}]" for index in range(len(sizes)))
        filters.append(f"[0:{video['index']}]split={len(sizes)}{labels}")
    for index, (width, height, _) in enumerate(sizes):
        label = f"in{index}" if len(sizes) > 1 else f"0:{video['index']}"
        filters.append(f"[{label}]scale={width}:{height},setsar=1[out{index}]")
    cmd.extend(["-filter_complex", ";".join(filters)])
    mappings = []
    for index, (_, _, bitrate) in enumerate(sizes):
        cmd.extend(["-map", f"[out{index}]", f"-b:v:{index}", bitrate])
        if audio is None:
            mappings.append(f"v:{index}")
        else:
            cmd.extend(["-map", f"0:{audio['index']}"])
            mappings.append(f"v:{index},a:{index}")
    cmd.extend([
        "-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "libx264",
        "-preset", "veryfast", "-pix_fmt", "yuv420p", "-threads:v", "1",
        "-sc_threshold", "0", "-force_key_frames:v", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
    ])
    if audio is not None:
        cmd.extend(["-c:a", "aac", "-ac", "2", "-b:a", "96k"])
    return cmd + [
        "-f", "hls", "-hls_time", str(SEGMENT_SECONDS), "-hls_playlist_type", "vod",
        "-hls_flags", "independent_segments", "-var_stream_map", " ".join(mappings),
        "-master_pl_name", "master.m3u8", "-hls_segment_filename", "v%v/seg-%06d.ts",
        "v%v/index.m3u8",
    ]


def stop_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    # The leader can exit before FFmpeg. Signal the group before releasing the lock.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


@telemetry.traced("ffmpeg.encode")
def encode(source, directory, lock_fd, cancelled, *, timeout):
    if cancelled.is_set():
        return "retry", "channel_lost"
    try:
        video, audio = select_streams(probe_video(source))
        sizes = rendition_sizes(video)
    except ProbeTimeout:
        return "retry", "probe_timeout"
    except InvalidVideo:
        return "failed", "invalid_video"

    if cancelled.is_set():
        return "retry", "channel_lost"
    command = command_for(source, video, audio, sizes)
    started = time.monotonic()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            ["timeout", "-k", "5", str(timeout), *command],
            cwd=directory, stdout=subprocess.DEVNULL, stderr=errors, start_new_session=True,
            pass_fds=(lock_fd,),
        )
        try:
            while process.poll() is None:
                if cancelled.wait(0.25):
                    return "retry", "channel_lost"
        finally:
            if process.poll() is None:
                stop_group(process)
        if process.returncode == 0:
            return "success", ""
        errors.seek(0)
        detail = errors.read(4096).decode("utf-8", errors="replace")
    logger.warning("FFmpeg failed (exit %s): %s", process.returncode, detail.strip())
    if "No space left on device" in detail:
        return "failed", "disk_full"
    if time.monotonic() - started >= timeout:
        return "failed", "ffmpeg_timeout"
    return "failed", "ffmpeg_decode_or_encode"


def filesystem_failure(error):
    if error.errno == errno.ENOSPC:
        return "failed", "disk_full"
    if error.errno in (errno.EACCES, errno.EPERM, errno.EROFS, errno.ENOENT):
        return "failed", "media_unavailable"
    return "retry", "media_io"
