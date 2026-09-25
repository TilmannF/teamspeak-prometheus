#!/usr/bin/env bash
# End-to-end test of the built image.
#
# HEALTHCHECK: every supported way of choosing the metrics port, and a proxy in
# the environment, must end healthy; a container without a running exporter
# must end unhealthy. No health or container log may contain the password, not
# even where it equals the metrics port.
#
# Shutdown: `docker stop` must end the exporter cleanly -- exit code 0 within a
# few seconds, not a SIGKILL after Docker's 10-second grace period.
#
#   tests/container_test.sh [image]      (default: teamspeak-prometheus:dev)
#
# Needs Docker. No TeamSpeak server: the exporter points at an address where
# nothing listens, which must not affect its health.

set -euo pipefail

IMAGE="${1:-teamspeak-prometheus:dev}"
PREFIX="tp-container-test-$$"
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
# A proxy for every HTTP request, as Docker's client-side proxy settings inject
# it; nothing listens on port 1. The loopback probe must not go through it.
start behind-proxy -e http_proxy=http://127.0.0.1:1 -e HTTP_PROXY=http://127.0.0.1:1 \
  -e all_proxy=http://127.0.0.1:1 --
# The password is the metrics port: healthcheck and exporter both print that
# number, and both must censor it.
start password-is-port -e METRICS_PORT=9400 -e TEAMSPEAK_PASSWORD=9400 --
# Variables set in the command line, not the container config: only the
# exporter's own environment has them. With exec the exporter is PID 1; without
# it, a child of the shell.
start inline-env -- sh -c 'METRICS_PORT=9500 TEAMSPEAK_PASSWORD=9500 exec python /app/app.py'
start inline-env-child -- sh -c 'export METRICS_PORT=9600; python /app/app.py; true'
start no-exporter -- sleep 300

expect default healthy
expect metrics-port-env healthy
expect metrics-port-flag healthy
expect behind-init healthy
expect shell-wrapper healthy
expect env-beats-flag healthy
expect behind-proxy healthy
expect password-is-port healthy
expect inline-env healthy
expect inline-env-child healthy
expect no-exporter unhealthy

# never_printed NAME VALUE: neither the health log nor the container log may
# contain VALUE.
never_printed() {
  local name="$PREFIX-$1" output
  output="$(docker inspect --format '{{range .State.Health.Log}}{{.Output}}{{end}}' "$name")"
  output+="$(docker logs "$name" 2>&1)"
  if [[ "$output" == *"$2"* ]]; then
    echo "FAIL $1: the password appears in the health or container log"
    FAILED=1
  else
    echo "ok   $1: password never printed"
  fi
}

never_printed password-is-port 9400
never_printed inline-env 9500
never_printed metrics-port-flag "$SECRET"

# The image holds the entry points, the requirements and the package's modules
# -- no caches, tests, docs or anything else from the build context.
contents="$(docker run --rm --entrypoint sh "$IMAGE" -c \
  'cd /app && find . -type f | sort | tr "\n" " "')"
expected="$(cd "$(dirname "$0")/.." && {
  printf '%s\n' ./app.py ./healthcheck.py ./requirements.txt
  find ./teamspeak_prometheus -name '*.py'
} | sort | tr '\n' ' ')"
if [[ "$contents" == "$expected" ]]; then
  echo "ok   image contents: only the exporter"
else
  echo "FAIL image contents"
  echo "  expected: $expected"
  echo "  found:    $contents"
  FAILED=1
fi

# stopped NAME: docker stop must finish fast with exit code 0 and a clean log.
stopped() {
  local name="$PREFIX-$1" started elapsed code
  started="$(date +%s)"
  docker stop "$name" >/dev/null
  elapsed=$(($(date +%s) - started))
  code="$(docker inspect --format '{{.State.ExitCode}}' "$name")"
  if [[ "$code" == 0 && "$elapsed" -le 5 ]] && docker logs "$name" 2>&1 | grep -q 'Stopped'; then
    echo "ok   $1: stopped in ${elapsed}s, exit code $code"
  else
    echo "FAIL $1: stopped in ${elapsed}s, exit code $code (want <= 5s, 0, 'Stopped' logged)"
    FAILED=1
  fi
}

stopped default
stopped behind-init
stopped shell-wrapper

exit "$FAILED"
