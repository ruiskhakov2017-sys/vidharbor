import json
import subprocess


class InvalidVideo(ValueError):
    pass


def ebml_document_type(path):
    # Matroska and WebM share one FFmpeg demuxer. Read their EBML DocType.
    with path.open("rb") as source:
        header = source.read(4096)

    def vint(offset, *, size):
        first = header[offset]
        length = 9 - first.bit_length()
        if length > 8 or offset + length > len(header):
            raise InvalidVideo("Некорректный заголовок EBML.")
        value = int.from_bytes(header[offset : offset + length], "big")
        if size:
            value ^= 1 << (7 * length)
        return value, offset + length

    try:
        tag, offset = vint(0, size=False)
        if tag != 0x1A45DFA3:
            return b""
        length, offset = vint(offset, size=True)
        end = min(offset + length, len(header))
        while offset < end:
            tag, offset = vint(offset, size=False)
            length, offset = vint(offset, size=True)
            if tag == 0x4282 and offset + length <= end:
                return header[offset : offset + length]
            offset += length
    except IndexError as exc:
        raise InvalidVideo("Некорректный заголовок EBML.") from exc
    return b""


def check_video(path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-protocol_whitelist", "file",
                "-format_whitelist", "mov,matroska,webm",
                "-max_alloc", "67108864",
                "-max_streams", "64",
                "-enable_drefs", "0",
                "-show_entries", "format=format_name:format_tags=major_brand:"
                "stream=codec_type,codec_name,width,height:stream_disposition=attached_pic",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            timeout=20,
        )
    except subprocess.TimeoutExpired as exc:
        raise InvalidVideo("Проверка видео не завершилась вовремя.") from exc
    if result.returncode or result.stderr:
        raise InvalidVideo("Не удалось прочитать контейнер или потоки видео.")

    metadata = json.loads(result.stdout)
    container = metadata.get("format", {})
    formats = set(container.get("format_name", "").split(","))
    brand = container.get("tags", {}).get("major_brand", "").strip().lower()
    if formats.intersection({"matroska", "webm"}):
        supported = ebml_document_type(path) in {b"matroska", b"webm"}
    else:
        # The MOV demuxer also recognizes other ISO BMFF profiles.
        supported = bool(formats.intersection({"mov", "mp4"})) and (
            brand in {"", "qt", "avc1", "dash", "m4v", "msnv", "isom"} or brand.startswith(("iso", "mp4"))
        )
    if not supported:
        raise InvalidVideo("Поддерживаются MP4, MOV, MKV и WebM.")

    streams = metadata.get("streams", [])
    if not any(
        stream.get("codec_type") == "video"
        and stream.get("codec_name")
        and stream.get("width", 0) > 0
        and stream.get("height", 0) > 0
        and not stream.get("disposition", {}).get("attached_pic")
        for stream in streams
    ):
        raise InvalidVideo("В файле не найден видеопоток.")
    if any(stream.get("codec_type") == "audio" and not stream.get("codec_name") for stream in streams):
        raise InvalidVideo("Не удалось распознать аудиопоток.")
