#!/usr/bin/env bash
set -euo pipefail
test -n "${SSHPASS:-}" || { echo 'VIDHARBOR_SERVER_PASSWORD is missing' >&2; exit 1; }
test -n "${PINNED_HOST_KEYS:-}" || { echo 'VIDHARBOR_DIAGNOSTIC_KNOWN_HOSTS is missing' >&2; exit 1; }
target=rul1@38.180.15.32
umask 077
local_stage=$(mktemp -d)
trap 'rm -rf -- "$local_stage"' EXIT
printf '%s\n' "$PINNED_HOST_KEYS" > "$local_stage/known_hosts"
unset PINNED_HOST_KEYS
ssh-keygen -F '[38.180.15.32]:63102' -f "$local_stage/known_hosts" > /dev/null
options=(-F /dev/null -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$local_stage/known_hosts"
         -o PubkeyAuthentication=no -o PreferredAuthentications=password,keyboard-interactive
         -o NumberOfPasswordPrompts=1 -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3
         -o ClearAllForwardings=yes)
if [[ "$APPLY_RELEASE" == true ]]; then
    stage=$(sshpass -e ssh "${options[@]}" -p 63102 "$target" 'umask 077; mktemp -d /tmp/vidharbor-portfolio.XXXXXXXX')
    [[ "$stage" =~ ^/tmp/vidharbor-portfolio\.[[:alnum:]]{8}$ ]]
    git archive "$DEPLOY_RELEASE" | gzip -n > "$local_stage/source.tgz"
    sshpass -e scp "${options[@]}" -P 63102 "$local_stage/source.tgz" "$target:$stage/source.tgz"
    remote="sudo -S -p '' -- flock -n -o /run/lock/vidharbor-deploy.lock bash -s -- $stage/source.tgz $DEPLOY_RELEASE"
    { printf '%s\n' "$SSHPASS"; cat scripts/portfolio-deploy.sh; } |
        sshpass -e ssh "${options[@]}" -p 63102 "$target" "$remote"
else
    { printf '%s\n' "$SSHPASS"; cat scripts/portfolio-preflight.sh; } |
        sshpass -e ssh "${options[@]}" -p 63102 "$target" "sudo -S -p '' -- bash -s"
fi
