# AGENTS.md

## Agent Instructions

Before modifying code, read:

1. `policies/00-engineering-policy.md`
2. `policies/20-python-code-policy.md`

These policies are normative. In case of conflict, the more specific Python policy overrides the general engineering policy.

Also read `README.md` and, when touching metrics, `docs/metrics.md`.

## Project Name

`teamspeak-prometheus`

Docker image: `tilmannf/teamspeak-prometheus`

## Project Goal

`teamspeak-prometheus` is a small read-only exporter. It polls a TeamSpeak 3 server over the ServerQuery protocol at a configurable interval and exposes the returned per-virtualserver counters as Prometheus gauges on an HTTP endpoint.

It is deliberately thin. TeamSpeak's numbers are passed through unconverted.

## What This Project Is Not

Do not add, unless explicitly requested:

* Any ServerQuery command that mutates server state (kick, ban, move, message, channel or permission edits).
* Bot, chat, or moderation features.
* A web UI, a dashboard server, or an API beyond `/metrics`.
* Persistence, a database, or its own alerting.
* Multi-server fan-out beyond the virtualservers of the one configured host.
* Telemetry or phone-home behavior.

The exporter reads. It does not administer.

## The Metric Contract

This is the most important rule in this repository.

The metric names in `METRICS_NAMES`, the `teamspeak_` prefix, and the `virtualserver_name` label are **public API**. So are the exporter's own `teamspeak_exporter_*` metrics and their labels. Users' Grafana dashboards and Prometheus alerting rules are keyed on them.

Agents MUST NOT rename, remove, reorder-into-renaming, re-prefix, or re-label a metric as a side effect of another task.

Changing the metric surface requires:

1. An explicit task asking for it.
2. An update to `docs/metrics.md` and `README.md`.
3. A note in the pull request describing what breaks for existing users.

Adding a new metric that TeamSpeak's `serverinfo` already returns is a small, allowed change — with a test.

## Secrets

The ServerQuery password MUST NOT be logged, printed, included in an error message, or written to a file.

The settings banner censors it today. Keep it censored.

Command-line errors are printed by argparse, outside logging; `_ArgumentParser` masks every value in them. Do not add argparse `type=` or `choices=`: flags are validated by the option parsers in `_OPTIONS`, after environment precedence.

Text from the TeamSpeak server is untrusted. It reaches the log only as arguments of log calls, where the `RedactingFilter` installed by `main()` censors the password and escapes control characters. Never pre-format server text into a log message, and never write it anywhere else unfiltered.

`--ts3password` exposes the password through the process list. The documentation MUST keep recommending `TEAMSPEAK_PASSWORD` instead. Do not remove the flag — it is existing public behavior.

This rule is not scoped to `app.py`. Test support, fakes, harnesses and scripts MUST NOT print a password either, even a fixture one — an exception that is visible in the tree is an exception the next change will copy.

Never commit a real host, password, or ServerQuery credential. Test fixtures use fake values.

## Architecture

```text
main()
  → parse_args()                    argparse
  → resolve_config()                pure: args + environment mapping → Config
  → build_gauges(registry)          41 gauges, explicit CollectorRegistry
  → build_exporter_metrics()        teamspeak_exporter_* self-metrics
  → start_http_server()             prometheus_client
  → poll_forever(), every --pollinterval (default 5s), backoff on failure:
        Teamspeak3MetricService.poll()   never raises
          ServerQueryClient              stdlib socket, login → serverlist
                                         → use → serverinfo per virtualserver
          update_gauges()                pure: serverinfo dict → gauges
```

See `docs/architecture.md`.

Everything above the `Teamspeak3MetricService` boundary is pure and directly testable. The TeamSpeak client is injected, and the ServerQuery client takes its connection as a parameter, so unit tests need no socket.

## How To Work

```bash
make setup        # virtualenv, runtime + dev dependencies
make check        # ruff check, ruff format --check, unit tests
make test-smoke   # end-to-end against the fake ServerQuery server
make run-fake     # run the exporter locally, no TeamSpeak server needed
make docker-build # verify the image still builds
make docker-test   # build the image, test its HEALTHCHECK and clean shutdown
```

No real TeamSpeak server is required to develop or verify anything in this repository. `tests/fake_ts3_server.py` is a real TCP ServerQuery stub.

Workflow for every change:

1. Read the existing code before modifying it.
2. Make the smallest useful change.
3. Add or update tests.
4. Run `make check`.
5. Report what changed, which checks ran, which did not, and what remains.

Do not rewrite unrelated files. Do not introduce dependencies without explaining why. Do not change public behavior silently.

## Scope Discipline

`docs/modernization-backlog.md` lists known weaknesses that are deliberately **not** being fixed yet.

Agents MUST NOT opportunistically pull a backlog item into an unrelated change. Each gets its own branch and its own pull request.

If you notice something new that belongs on that list, add it to the list rather than fixing it in passing.

## Branching and Pull Requests

Work happens on a branch, never directly on `master`.

Follow-up milestones stack: branch off the previous milestone's branch, not `master`, so each pull request's diff stays scoped to its own milestone.

One purpose per pull request. Concise commit subjects, lowercase, imperative:

```text
add ci workflow
pin ts3 dependency
make app.py importable and testable
```

Avoid `misc`, `stuff`, `updates`, `ai changes`.

## Workflow Artifact Policy

Local planning and review artifacts under `.work/<feature-slug>/` are workflow state for the active agent session. They stay on disk for continuity but are ignored by Git and MUST NOT be committed.

## Human Review Checkpoints

Stop and ask before:

* Changing, removing, or re-labelling any existing metric.
* Replacing the TeamSpeak client library.
* Adding any ServerQuery command that writes.
* Changing the default ports, environment variable names, or CLI flags.
* Publishing a Docker image tag.
* Adding a dependency that is unmaintained or has an incompatible license.

## Current Default Decisions

```text
Language:            Python 3.12+ (image runs 3.14)
Runtime deps:        prometheus_client only; ServerQuery client is in app.py
Linter/formatter:    ruff
Tests:               pytest
Entry point:         app.py (single module, intentionally)
Healthcheck:         healthcheck.py, container-only, reuses app's config resolution
Poll interval:       5s, --pollinterval / TEAMSPEAK_POLL_INTERVAL
Container:           python:3.14-alpine, non-root
Metrics port:        8000
ServerQuery port:    10011
License:             MIT
CI:                  GitHub Actions (lint, test, docker build)
```

## Status

The 2026 modernization is done: an in-repo ServerQuery client replaced the archived library, the image runs Python 3.14 as non-root, output goes through `logging`, the poll interval is configurable, and polls survive connection, login, flood and missing-field errors while reporting them through `teamspeak_exporter_*` metrics. What is left is in `docs/modernization-backlog.md`.
