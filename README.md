# teamspeak-prometheus

A small, read-only Prometheus exporter for TeamSpeak 3.

[![CI](https://github.com/TilmannF/teamspeak-prometheus/actions/workflows/ci.yml/badge.svg)](https://github.com/TilmannF/teamspeak-prometheus/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/TilmannF/teamspeak-prometheus)](LICENSE.md)
[![OpenSSF Scorecard](https://api.securityscorecards.dev/projects/github.com/TilmannF/teamspeak-prometheus/badge)](https://scorecard.dev/viewer/?uri=github.com/TilmannF/teamspeak-prometheus)
[![Docker Pulls](https://img.shields.io/docker/pulls/tilmannf/teamspeak-prometheus)](https://hub.docker.com/r/tilmannf/teamspeak-prometheus)

## What it does

It polls a TeamSpeak 3 server over the ServerQuery protocol at a configurable
interval and exposes the returned per-virtualserver counters as Prometheus
gauges on an HTTP `/metrics` endpoint. TeamSpeak's numbers are passed through
unconverted.

It keeps running when TeamSpeak does not: connection errors, rejected logins,
flood throttling and missing fields are logged, retried with backoff, and
reported through its own `teamspeak_exporter_*` metrics.

## What it deliberately does not do

This exporter reads. It does not administer. It will not gain, even on
request:

* Any ServerQuery command that mutates server state (kick, ban, move,
  message, channel or permission edits)
* Bot, chat, or moderation features
* A web UI, a dashboard server, or an API beyond `/metrics`
* Persistence, a database, or its own alerting
* Multi-server fan-out beyond the virtualservers of the one configured host
* Telemetry or phone-home behavior

See `AGENTS.md` for the full policy this project (and any AI agent working on
it) is held to.

## Quickstart

```bash
docker run -d -p 8000:8000 \
  -e TEAMSPEAK_HOST=example.com \
  -e TEAMSPEAK_PASSWORD=example123 \
  ghcr.io/tilmannf/teamspeak-prometheus:1.0.0
```

Or from Docker Hub:

```bash
docker run -d -p 8000:8000 \
  -e TEAMSPEAK_HOST=example.com \
  -e TEAMSPEAK_PASSWORD=example123 \
  tilmannf/teamspeak-prometheus:1.0.0
```

Pin a version tag in anything beyond local experimentation — see
[CHANGELOG.md](CHANGELOG.md) for what changed and
[docs/releasing.md](docs/releasing.md) for what the version number means.

## Configuration

### Environment variables (recommended)

Environment variables take precedence over the command-line arguments below;
the exporter logs a warning for every flag an environment variable overrides.

| Name | Description | Default value |
| --- | --- | --- |
| `TEAMSPEAK_HOST` | Hostname or ip address of TS3 server | *localhost* |
| `TEAMSPEAK_PORT` | Port of TS3 server | *10011* |
| `TEAMSPEAK_USERNAME` | ServerQuery username of TS3 server | *serveradmin* |
| `TEAMSPEAK_PASSWORD` | ServerQuery password of TS3 server |  |
| `METRICS_PORT` | Port on which this service exposes the metrics | *8000* |
| `TEAMSPEAK_POLL_INTERVAL` | Seconds between two polls of the TS3 server, at most 86400 | *5* |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR` | *INFO* |

### Command-line arguments

List all arguments with `python app.py -h`.

| Name | Description | Default value |
| --- | --- | --- |
| `--ts3host` | Hostname or ip address of TS3 server | *localhost* |
| `--ts3port` | Port of TS3 server | *10011* |
| `--ts3username` | ServerQuery username of TS3 server | *serveradmin* |
| `--ts3password` | ServerQuery password of TS3 server |  |
| `--metricsport` | Port on which this service exposes the metrics | *8000* |
| `--pollinterval` | Seconds between two polls of the TS3 server, at most 86400 | *5* |
| `--loglevel` | `DEBUG`, `INFO`, `WARNING` or `ERROR` | *INFO* |

Flags are only checked when they are used: a malformed flag that an
environment variable overrides is ignored with a warning.

**Prefer `TEAMSPEAK_PASSWORD` over `--ts3password`.** A command-line argument
is visible to anything that can read the process list (`ps`, `/proc/<pid>/cmdline`);
the environment variable is not on the command line, though it is still
readable by the same user, root, or `docker inspect` on the container.

### ServerQuery allowlist

TeamSpeak throttles query clients that are not on its allowlist — by default
10 commands per 3 seconds. One poll needs `3 + 2 × virtualservers` commands.
The exporter waits and retries when throttled, but on a host with several
virtualservers, add the exporter's IP to `query_ip_allowlist.txt` on the
TeamSpeak server. If TeamSpeak has already banned the IP, the exporter logs
`closed the connection before greeting` until the ban expires (default 10
minutes).

## Deployment

### Docker Compose

```yaml
services:
  teamspeak-prometheus:
    image: tilmannf/teamspeak-prometheus:1.0.0
    environment:
      TEAMSPEAK_HOST: example.com
      TEAMSPEAK_PASSWORD: example123
    ports:
      - 8000:8000
```

### Container health

The image runs as a non-root user (UID 10001), stops cleanly on `docker stop`
(SIGTERM), and has a Docker `HEALTHCHECK`.
Healthy means the metrics endpoint answers — on whichever port the exporter
actually uses, whether set with `METRICS_PORT` or with `--metricsport` in an
overridden container command, also behind `--init` or a shell wrapper. It
always talks to the exporter directly, ignoring any `HTTP_PROXY` in the
container environment.

The container stays healthy while TeamSpeak is unreachable, on purpose: a
TeamSpeak outage should not get the exporter restarted. Alert on
`teamspeak_exporter_poll_success` or
`teamspeak_exporter_last_successful_poll_timestamp_seconds` for that instead.

The result of the last checks, including the port probed, is shown by:

```bash
docker inspect --format '{{json .State.Health}}' <container>
```

Kubernetes ignores Docker's `HEALTHCHECK`; there, use an HTTP `livenessProbe`
on `/metrics` and the metrics port.

### Prometheus scrape config

```yaml
- job_name: teamspeak
  honor_timestamps: true
  scrape_interval: 15s
  scrape_timeout: 10s
  metrics_path: /metrics
  scheme: http
  static_configs:
  - targets:
    - example.com:8000
```

## Metrics

Every TeamSpeak metric is prefixed with `teamspeak_` and labelled with
`virtualserver_name`; the exporter's own health is under
`teamspeak_exporter_` (last poll time, success, errors, missing fields). See
[docs/metrics.md](docs/metrics.md) for the full list — **these names are a stable contract**; existing dashboards and
alerting rules are keyed on them, and a rename or removal is a breaking
change (see [CHANGELOG.md](CHANGELOG.md)).

A Grafana dashboard demonstrating a subset of the metrics is committed at
`grafana-dashboard.json`.

## Development

No TeamSpeak server is required to work on this project — the test suite
ships a fake ServerQuery server.

```bash
make setup     # virtualenv + runtime and dev dependencies
make check     # lint, format check, unit tests
make run-fake  # run the exporter against the fake TS3 server
make docker-test  # build the image, test healthcheck and shutdown (needs Docker)
```

See [docs/testing.md](docs/testing.md) for details, [docs/architecture.md](docs/architecture.md)
for how it fits together, and [CONTRIBUTING.md](CONTRIBUTING.md) before
opening a pull request.

## Project

* **Versioning**: [SemVer](https://semver.org/), see [CHANGELOG.md](CHANGELOG.md)
  and [docs/releasing.md](docs/releasing.md)
* **Contributing**: [CONTRIBUTING.md](CONTRIBUTING.md)
* **Code of Conduct**: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
* **Security**: [SECURITY.md](.github/SECURITY.md) — private vulnerability
  reporting, please, not a public issue
* **AI disclosure**: this project is developed with AI assistance under
  human direction — see [docs/ai.md](docs/ai.md); the rules the models
  follow live in [AGENTS.md](AGENTS.md) and [policies/](policies/)
* **License**: [MIT](LICENSE.md)
* **Trademark**: TeamSpeak is a registered trademark of TeamSpeak Systems
  GmbH. This project is not endorsed by or affiliated with TeamSpeak Systems
  GmbH in any way.
* **Contact**: [GitHub](https://github.com/TilmannF) ·
  [Twitter](https://twitter.com/TilmannFelgner)
