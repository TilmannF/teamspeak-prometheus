# Modernization backlog

Known weaknesses, deliberately **not** fixed yet. The repository was AI-enabled
first — policies, tests, tooling, CI — so that this work can be done in small,
verifiable steps.

Rules for agents:

* Do not pull an item into an unrelated change (AGENTS.md, "Scope Discipline").
* One item per branch, one pull request per branch, stacked on the previous.
* If you find a new weakness, add it here instead of fixing it in passing.

## 1. Replace the archived TeamSpeak client — blocking

`requirements.txt` pins `nikdoof/python-ts3` at a 2015 commit of an archived
repository. It imports `telnetlib`, **removed in Python 3.13**, so the exporter
cannot run on any interpreter newer than 3.12 and the base image can never be
bumped past `python:3.12-alpine`.

Options: the maintained `ts3` package on PyPI (different API), or a small
in-repo ServerQuery client — `tests/query_client.py` is already a working
prototype of exactly that, at about 70 lines.

This is the one item that gets worse with time.

## 2. `print` to `logging`

All output is `print`. No levels, no timestamps, no way to quieten it. Converting
also removes the `UP031` ruff ignore in `pyproject.toml`.

The password censoring in the startup banner must survive the conversion.

## 3. No retry, no connection reuse

Every 5 seconds the exporter opens a connection, logs in, reads, and drops it. A
single refused connection or dropped socket takes the process down and relies on
Docker restarting it. Prometheus sees a gap either way, but a bounded retry with
backoff would turn most transient failures into a missed sample instead of a
crash loop.

## 4. Hardcoded poll interval

`READ_INTERVAL_IN_SECONDS = 5` is a module constant. It should be a flag and an
environment variable like everything else — and 5s is aggressive relative to a
typical 15s scrape interval.

## 5. Configuration has two sources that silently disagree

Environment variables override command-line flags with no warning, so
`--ts3host` on the command line of a container that also sets `TEAMSPEAK_HOST`
does nothing. It is long-standing documented behavior; changing it is a breaking
change and needs a deprecation path.

## 6. A missing `serverinfo` field crashes the exporter

`update_gauges` indexes `serverinfo[name]` directly, so a TeamSpeak version that
stops returning one of the 41 fields takes the process down instead of skipping
that metric. Deciding between "skip and warn" and "fail loudly" is a behavior
change, hence not done here.

## 7. Stale series after a virtualserver is removed

Virtualservers are rediscovered every poll, but gauges for a virtualserver that
disappears keep their last value until restart. Clearing them needs the exporter
to track the previous label set.

## 8. No health or self-observability

There is no `/healthz`, and nothing reports scrape errors, the last successful
poll, or the exporter's own version. An operator cannot distinguish "TeamSpeak
is quiet" from "the exporter has been failing for an hour".

## 9. Container hardening

The image runs as root, installs `git` only to fetch the archived dependency
(item 1), has no `HEALTHCHECK`, and is built for a single architecture. There is
also a stray typo in the `apk` cleanup line of the `Dockerfile`.

## 10. Publishing

No automated image build or tag publishing, and no dependency update automation
(Dependabot or Renovate).

## 11. Grafana dashboard

`grafana-dashboard.json` is committed but was never published to grafana.com, so
the README could only reference a placeholder ID. Either publish it or keep
pointing at the file.
