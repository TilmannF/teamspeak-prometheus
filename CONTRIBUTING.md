# Contributing

Thanks for looking. This is a small read-only TeamSpeak 3 → Prometheus exporter.
It is [developed with AI assistance](docs/ai.md) under human direction.
Contributions can come from humans or models. The bar is the same.

## Before you write code

1. Read [README.md](README.md) and [docs/architecture.md](docs/architecture.md).
2. `AGENTS.md` and `policies/` are the product rules. Read-only, no server
   administration commands, no bot features, and the metric names are a contract.
3. Check [docs/modernization-backlog.md](docs/modernization-backlog.md) — the
   known weaknesses are tracked there on purpose. One item per pull request.
4. Open an issue first if the change is more than a small fix.

## How to work

No TeamSpeak server is needed for any of this:

```bash
make setup
make check        # ruff check, ruff format --check, unit tests
make test-smoke   # end-to-end against the fake ServerQuery server
```

See [docs/testing.md](docs/testing.md).

## Will not be accepted

- Renaming, removing, or re-labelling an existing metric as part of another
  change — it breaks every dashboard and alert built on this exporter
- ServerQuery commands that change server state (kick, ban, move, message,
  permission or channel edits)
- Bot, chat, or moderation features
- Anything that logs, prints, or stores the ServerQuery password
- Telemetry or phone-home behavior
- Drive-by refactors with no tests

## Pull requests

- One purpose per pull request
- Tests for behavior changes
- `make check` green; CI runs the same checks plus the smoke tests and a Docker
  build
- Say what you did not run, and why
