# Modernization backlog

Known weaknesses, deliberately **not** fixed yet.

The 2026 modernization closed the original items: the archived TeamSpeak client
library, `print` output, crashing on connection errors, the hardcoded poll
interval, silent config overrides, crashing on missing `serverinfo` fields,
stale series of removed virtualservers, missing self-observability, and the
root container without a health check.

Rules for agents:

* Do not pull an item into an unrelated change (AGENTS.md, "Scope Discipline").
* If you find a new weakness, add it here instead of fixing it in passing.

## 1. Configuration precedence

Environment variables still override command-line flags. Since 1.0.0 the
exporter warns when that happens, but flipping the precedence (flags win, as in
most tools) is a breaking change and needs a major version.

## 2. Grafana dashboard

`grafana-dashboard.json` is committed but was never published to grafana.com,
and it has no panel for the `teamspeak_exporter_*` health metrics yet.

## 3. TeamSpeak 6

TeamSpeak 6 (in beta as of 2026-09) has no raw ServerQuery; it offers SSH query
and HTTP WebQuery, plus its own Prometheus endpoint. Supporting it would mean a
WebQuery transport (TS3 3.12+ has WebQuery too, but a read-scope API key cannot
call `serverinfo`, so it needs a `manage` key). Revisit when TS6 is stable.

## 4. Supply-chain polish

The base image is pinned by tag, not digest, and runtime dependencies are not
hash-pinned. Both are Scorecard findings, neither is urgent with a single
runtime dependency.
