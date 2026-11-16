FROM python:3.13-alpine@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca AS base

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --disable-pip-version-check --only-binary=:all: -r requirements.txt \
    && install -d -o 65532 -g 65532 -m 0700 \
        /var/lib/vidharbor /var/lib/vidharbor/uploads /var/lib/vidharbor/original /var/lib/vidharbor/locks \
        /var/lib/vidharbor-journal
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "2", "--timeout", "12", "vidharbor.wsgi:application"]

FROM base AS app
COPY manage.py .
COPY vidharbor/ vidharbor/
USER 65532:65532

FROM base AS upload
# BusyBox timeout detaches a watchdog that inherits flock outside the FFmpeg group.
# GNU timeout keeps the monitor and writer in the group stopped by transcode.py.
RUN apk add --no-cache ffmpeg=8.1.3-r0 coreutils=9.11-r0
COPY --from=app /app/ /app/
USER 65532:65532
