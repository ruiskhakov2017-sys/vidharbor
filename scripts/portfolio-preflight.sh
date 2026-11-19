#!/usr/bin/env bash
set -euo pipefail
gateway=axyro-work-reverse-proxy-1
config=/opt/axyro-work/release/infrastructure/migration/Caddyfile.domain-ready
mountpoint -q /srv/vidharbor-data
echo 'VidHarbor services and Compose release'
docker ps --filter label=com.docker.compose.project=vidharbor-public --format '{{.Names}} {{.Image}} {{.Status}}'
docker inspect --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}' vidharbor-public-web-1
echo 'Caddy site and route markers'
grep -nE '^[[:space:]]*([^# ]+[.]tech[^#]*\{|import (portfolio_path|public_showcase))|/vidharbor|vidharbor-public-web' "$config" || true
printf 'Docker free KiB: '
df -Pk "$(docker info --format '{{.DockerRootDir}}')" | awk 'NR==2 {print $4}'
echo 'Preflight complete'
