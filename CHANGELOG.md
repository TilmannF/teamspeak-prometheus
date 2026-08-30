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

- Multi-architecture (`linux/amd64`, `linux/arm64`) container images, published
  to GHCR and Docker Hub on tagged release, with build provenance attestation
  and an SBOM.
- Community and security documentation: `CODE_OF_CONDUCT.md`, `SECURITY.md`,
  issue forms, `CODEOWNERS`.
- CI hardening: pinned GitHub Actions, dependency review, CodeQL, and OpenSSF
  Scorecard.
- Dependabot for GitHub Actions, Python, and the base image.
- A test suite, `ruff` linting, and a fake ServerQuery server for end-to-end
  testing without a real TeamSpeak server.

### Known limitations

Tracked in `docs/modernization-backlog.md`: the archived TeamSpeak client
library (blocks Python 3.13+), `print`-based logging, no connection retry, a
hardcoded 5-second poll interval, and container hardening gaps (runs as root,
no `HEALTHCHECK`). None of these affect the metric contract.

[1.0.0]: https://github.com/TilmannF/teamspeak-prometheus/releases/tag/v1.0.0
