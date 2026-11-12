#!/bin/sh
set -eu

export RABBITMQ_DEFAULT_PASS="$(cat /run/secrets/rabbitmq_password)"
export RABBITMQ_CTL_ERL_ARGS="+S 1:1 +A 1"
rm -f /tmp/vidharbor-policy-ready

docker-entrypoint.sh rabbitmq-server &
broker_pid=$!
trap 'kill -TERM "$broker_pid" 2>/dev/null || true; wait "$broker_pid" || true; exit 0' TERM INT

attempt=0
until su-exec rabbitmq rabbitmqctl -q await_startup >/dev/null 2>&1; do
  if ! kill -0 "$broker_pid" 2>/dev/null; then
    wait "$broker_pid"
    exit 1
  fi
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "RabbitMQ did not start in time" >&2
    exit 1
  fi
  sleep 5
done
su-exec rabbitmq rabbitmqctl set_policy --apply-to quorum_queues vidharbor-jobs '^video[.]jobs$' '{"dead-letter-exchange":"video.dead","dead-letter-routing-key":"video.malformed.v1","dead-letter-strategy":"at-least-once","overflow":"reject-publish","consumer-timeout":14400000}'
touch /tmp/vidharbor-policy-ready
wait "$broker_pid"
