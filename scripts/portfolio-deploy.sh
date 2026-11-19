#!/usr/bin/env bash
set -euo pipefail
umask 077

archive=$1
sha=$2
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
old=/opt/vidharbor/releases/3f15d7c48896e8bf47603958e2871afc824c5980
release="/opt/vidharbor/releases/$sha"
gateway=axyro-work-reverse-proxy-1
config=/opt/axyro-work/release/infrastructure/migration/Caddyfile.domain-ready
domain=axyro.tech
mountpoint -q /srv/vidharbor-data
test ! -e "$release"
test "$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' vidharbor-public-web-1)" = "$old"
work=$(mktemp -d /tmp/vidharbor-rollout.XXXXXXXX)
candidate="/etc/caddy/.${work##*/}.Caddyfile"
services=(api upload worker web)
old_compose=(docker compose --project-directory "$old" -p vidharbor-public --env-file "$old/.env"
    -f "$old/compose.yaml" -f "$old/config/deployment/compose.public.yaml.example" -f "$old/compose.server.yml")
new_compose=(docker compose --project-directory "$release" -p vidharbor-public --env-file "$release/.env"
    -f "$release/compose.yaml" -f "$release/config/deployment/compose.public.yaml.example" -f "$release/compose.server.yml"
    -f "$release/compose.rollout.yml")
changed_services=0
changed_route=0
active() { docker exec "$gateway" wget -qO- http://127.0.0.1:2019/config/; }
private_health() {
    docker exec "$gateway" wget -qO- --header="Host: $1" http://vidharbor-public-web/health/ready |
        python3 -c 'import json,sys; assert json.load(sys.stdin)["status"]=="ready"'
}
curl_public() { curl -fsS --connect-timeout 5 --max-time 15 "https://$domain$1"; }
cleanup() {
    local rc=$?
    trap - EXIT
    if (( rc != 0 )); then
        if (( changed_route )); then
            if cmp -s "$config" "$work/next.Caddyfile" &&
               cat "$work/old.Caddyfile" > "$config" &&
               docker exec -i "$gateway" caddy reload --config - < "$work/live.json"; then
                echo 'Previous Caddy routes restored.'
            else
                echo "Caddy rollback requires attention. Backup: $work" >&2
            fi
        fi
        if (( changed_services )); then
            if "${old_compose[@]}" up --no-build --no-deps -d --wait --wait-timeout 150 "${services[@]}" &&
                private_health "$old_host"; then
                echo 'Previous VidHarbor services restored.'
            else
                echo "VidHarbor rollback requires attention. Backup: $work" >&2
            fi
        fi
    fi
    docker exec "$gateway" rm -f -- "$candidate" >/dev/null 2>&1 || true
    # Keep Caddy backups in a root-only directory for later inspection.
    echo "Caddy backup directory: $work"
    exit "$rc"
}
trap cleanup EXIT

old_host=$(sed -n 's/^VIDHARBOR_PUBLIC_HOST=//p' "$old/.env")
[[ "$old_host" =~ ^[a-z0-9.-]+$ ]]
private_health "$old_host"
for path in / /api/ready /portfolio/api/ready; do
    curl_public "$path" >/dev/null
done

install -d -m 0755 "$release"
tar -xzf "$archive" --no-same-owner -C "$release"
cp "$old/compose.server.yml" "$release/compose.server.yml"
sed -E '/^VIDHARBOR_(PUBLIC_HOST|PUBLIC_BASE_URL|BASE_PATH|DEMO_STUDIO)=/d' "$old/.env" > "$release/.env"
cat >> "$release/.env" <<'ENV'
VIDHARBOR_PUBLIC_HOST=axyro.tech
VIDHARBOR_PUBLIC_BASE_URL=https://axyro.tech
VIDHARBOR_BASE_PATH=/vidharbor/
VIDHARBOR_DEMO_STUDIO=1
ENV
printf 'services:\n' > "$release/compose.rollout.yml"
for service in "${services[@]}"; do
    printf '  %s:\n    image: vidharbor-portfolio-%s:%s\n' "$service" "$service" "$sha" >> "$release/compose.rollout.yml"
done
"${new_compose[@]}" config --quiet

cp "$config" "$work/old.Caddyfile"
active > "$work/live.json"
docker exec "$gateway" caddy adapt --config /etc/caddy/Caddyfile --adapter caddyfile > "$work/old.json"
python3 - "$work" "$release/config/deployment/vidharbor-path.caddy.example" <<'PY'
import json
import sys
from pathlib import Path

work, snippet = map(Path, sys.argv[1:])
source = (work / "old.Caddyfile").read_text()
marker = "http://axyro.tech, https://axyro.tech {\n"
if source.count(marker) != 1 or "/vidharbor" in source or "vidharbor-public-web" in source:
    raise SystemExit("Unexpected live Caddyfile site or route layout")
if json.loads((work / "old.json").read_text()) != json.loads((work / "live.json").read_text()):
    raise SystemExit("Caddyfile does not match the running config")
indented = "".join(f"    {line}\n" for line in snippet.read_text().splitlines() if not line.startswith("#"))
(work / "next.Caddyfile").write_text(source.replace(marker, marker + indented))
PY
docker exec -i "$gateway" sh -c 'umask 077; set -C; cat > "$1"' sh "$candidate" < "$work/next.Caddyfile"
docker exec "$gateway" caddy validate --config "$candidate" --adapter caddyfile
docker exec "$gateway" caddy adapt --config "$candidate" --adapter caddyfile > "$work/next.json"
python3 - "$work" <<'PY'
import json
import re
import sys
from pathlib import Path

work = Path(sys.argv[1])
old, new = (json.loads((work / f"{name}.json").read_text()) for name in ("old", "next"))
removed = set()

def remove_portfolio_route(value, axyro=False):
    if isinstance(value, dict):
        axyro = axyro or value.get("match") == [{"host": ["axyro.tech"]}]
        for key, child in value.items():
            if key == "routes" and axyro:
                for route in child[:]:
                    if route.get("match") in ([{"path": ["/vidharbor"]}], [{"path": ["/vidharbor/*"]}]):
                        path = route["match"][0]["path"][0]
                        if path == "/vidharbor/*" and "vidharbor-public-web:80" not in json.dumps(route):
                            raise ValueError("Incorrect VidHarbor upstream")
                        removed.add(path)
                        child.remove(route)
            remove_portfolio_route(child, axyro)
    elif isinstance(value, list):
        for child in value:
            remove_portfolio_route(child, axyro)

def normalize_groups(value, groups):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "group" and isinstance(child, str) and re.fullmatch(r"group[0-9]+", child):
                value[key] = groups.setdefault(child, len(groups))
            else:
                normalize_groups(child, groups)
    elif isinstance(value, list):
        for child in value:
            normalize_groups(child, groups)

remove_portfolio_route(new)
normalize_groups(old, {})
normalize_groups(new, {})
if removed != {"/vidharbor", "/vidharbor/*"} or old != new:
    raise SystemExit("Caddy validation rejected a change outside the two planned routes")
print("Caddy route comparison passed.")
PY
export COMPOSE_PARALLEL_LIMIT=1
"${new_compose[@]}" build "${services[@]}"
idle() {
    "${old_compose[@]}" exec -T --interactive=false api python manage.py shell -c '
from django.utils import timezone
from datetime import timedelta
from vidharbor.models import Job, Upload
if Job.objects.filter(state__in=("QUEUED","PROCESSING","RETRY_WAIT")).exists() or Upload.objects.filter(state__in=("UPLOADING","VERIFYING"), updated_at__gte=timezone.now()-timedelta(minutes=5)).exists():
    raise SystemExit("Active uploads or video processing, cutover postponed")'
}
idle
cmp -s "$config" "$work/old.Caddyfile"
active > "$work/live-now.json"
cmp -s "$work/live-now.json" "$work/live.json"
changed_services=1
"${old_compose[@]}" stop web
idle
"${new_compose[@]}" up --no-build --no-deps -d --wait --wait-timeout 150 "${services[@]}"
private_health "$domain"
cmp -s "$config" "$work/old.Caddyfile"
active > "$work/live-now.json"
cmp -s "$work/live-now.json" "$work/live.json"
changed_route=1
cat "$work/next.Caddyfile" > "$config"
docker exec -i "$gateway" caddy reload --config - < "$work/next.json"
for path in / /api/ready /portfolio/api/ready; do
    curl_public "$path" >/dev/null
done
curl_public /vidharbor/health/ready |
    python3 -c 'import json,sys; assert json.load(sys.stdin)=={"status":"ready","checks":{"postgres":True,"rabbitmq":True}}'
curl_public /vidharbor/ > "$work/index.html"
grep -Eq '/vidharbor/assets/.*\.js' "$work/index.html"
curl_public /vidharbor/api/auth/session |
    python3 -c 'import json,sys; assert json.load(sys.stdin)["demo_studio"] is True'
echo 'DEPLOYED: https://axyro.tech/vidharbor/'
