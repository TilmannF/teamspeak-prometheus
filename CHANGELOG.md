# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versioning
follows [Semantic Versioning](https://semver.org/).

The metric surface — the names in `METRICS_NAMES`, the `teamspeak_` prefix, and
the `virtualserver_name` label — is the contract this project's SemVer is
measured against. A metric rename, removal, re-prefix, or re-label is a major
version bump. See `AGENTS.md`, "The Metric Contract".

## [1.0.0]

This is the first tagged release of a project that has existed, untagged, for
six years. It is not a list of changes since a predecessor — there wasn't one.
It is the state as shipped: what a user gets if they pull this tag today.

### Added

- `--pollinterval` / `TEAMSPEAK_POLL_INTERVAL` (default 5 seconds, unchanged)
  and `--loglevel` / `LOG_LEVEL`.
- Exporter self-metrics: `teamspeak_exporter_last_poll_timestamp_seconds`,
  `teamspeak_exporter_last_successful_poll_timestamp_seconds`,
  `teamspeak_exporter_poll_success`, `teamspeak_exporter_poll_duration_seconds`,
  `teamspeak_exporter_poll_errors_total{reason}`,
  `teamspeak_exporter_missing_fields{virtualserver_name}`,
  `teamspeak_exporter_build_info{version}`.
- A warning at startup for every flag an environment variable overrides.
- Container `HEALTHCHECK` that probes `/metrics` on the port the exporter
  actually uses (`METRICS_PORT` or `--metricsport`), never through a proxy
  from the environment, and deliberately does not depend on TeamSpeak being
  reachable.
- Multi-architecture (`linux/amd64`, `linux/arm64`) container images, published
  to GHCR and Docker Hub on tagged release, with build provenance attestation
  and an SBOM.
- Community and security documentation: `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  issue forms, `CODEOWNERS`.
- CI hardening: pinned GitHub Actions, dependency review, CodeQL, and OpenSSF
  Scorecard.
- Dependabot for GitHub Actions, Python, and the base image.
- A test suite, `ruff` linting, and a fake ServerQuery server for end-to-end
  testing without a real TeamSpeak server, checked against responses captured
  from TeamSpeak 3.13.8.

### Changed

- The archived `python-ts3` library is replaced by a small built-in
  ServerQuery client (standard library only). The only runtime dependency left
  is `prometheus_client`.
- The image runs Python 3.14 (was 3.12), as a non-root user, without `git`.
- Output goes through `logging` with timestamps and levels.
- The exporter no longer exits on connection errors, rejected logins, or
  ServerQuery errors: it logs, counts the error, and retries with backoff
  (doubling, capped at 60 seconds or the poll interval if that is longer).
- The poll interval is measured start to start.

### Fixed

- On hosts with four or more virtualservers, TeamSpeak's flood protection
  throttled every poll and the exporter silently skipped the remaining
  virtualservers. It now waits and retries as TeamSpeak asks.
- One failing virtualserver (for example a stopped one) no longer aborts the
  poll for the others.
- A missing or non-numeric `serverinfo` field no longer crashes the exporter;
  only that series is left out.
- Series of removed virtualservers are dropped instead of keeping their last
  value until restart.
- ServerQuery reads have a 10-second timeout instead of none.

[1.0.0]: https://github.com/TilmannF/teamspeak-prometheus/releases/tag/v1.0.0
