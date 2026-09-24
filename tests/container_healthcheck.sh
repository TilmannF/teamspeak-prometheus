#!/usr/bin/env bash
# End-to-end test of the image's HEALTHCHECK: every supported way of choosing
# the metrics port must end healthy, and a container without a running
# exporter must end unhealthy.
#
#   tests/container_healthcheck.sh [image]      (default: teamspeak-prometheus:dev)
#
# Needs Docker. No TeamSpeak server: the exporter points at an address where
# nothing listens, which must not affect its health.

set -euo pipefail

IMAGE="${1:-teamspeak-prometheus:dev}"
PREFIX="tp-healthcheck-$$"
# A fixture value, only ever compared against, never printed.
SECRET="fixture-password-$$"
CONTAINERS=()
FAILED=0

# shellcheck disable=SC2329  # invoked by the EXIT trap below
cleanup() {
  if ((${#CONTAINERS[@]})); then
    docker rm -f "${CONTAINERS[@]}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

# start NAME [docker run options...] -- [command...]
start() {
  local name="$PREFIX-$1"
  shift
  local options=()
  while (($#)) && [[ "$1" != "--" ]]; do
    options+=("$1")
    shift
  done
  shift || true
  docker run -d --name "$name" \
    --health-interval=1s --health-start-period=15s --health-retries=3 \
    -e TEAMSPEAK_HOST=127.0.0.1 -e TEAMSPEAK_PORT=1 \
    ${options[@]+"${options[@]}"} "$IMAGE" "$@" >/dev/null
  CONTAINERS+=("$name")
}

# expect NAME healthy|unhealthy
expect() {
  local name="$PREFIX-$1" want="$2" status=""
  for _ in $(seq 60); do
    status="$(docker inspect --format '{{.State.Health.Status}}' "$name")"
    [[ "$status" == "$want" ]] && break
    sleep 1
  done
  local log
  log="$(docker inspect --format '{{range .State.Health.Log}}{{.Output}}{{end}}' "$name")"
  if [[ "$log" == *"$SECRET"* ]]; then
    echo "FAIL $1: the healthcheck output contains the password"
    FAILED=1
  elif [[ "$status" == "$want" ]]; then
    echo "ok   $1: $status ($(tail -n 1 <<<"$log"))"
  else
    echo "FAIL $1: expected $want, got $status"
    echo "$log" | tail -n 3
    FAILED=1
  fi
}

start default --
start metrics-port-env -e METRICS_PORT=9200 --
start metrics-port-flag -- python /app/app.py --metricsport 9100 --ts3password "$SECRET"
start behind-init --init -- python /app/app.py --metricsport 9100
start shell-wrapper -- sh -c 'python /app/app.py --metricsport 9100'
start env-beats-flag -e METRICS_PORT=9200 -- python /app/app.py --metricsport 9100
start no-exporter -- sleep 300

expect default healthy
expect metrics-port-env healthy
expect metrics-port-flag healthy
expect behind-init healthy
expect shell-wrapper healthy
expect env-beats-flag healthy
expect no-exporter unhealthy

exit "$FAILED"
