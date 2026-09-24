# Metrics

Every TeamSpeak metric this exporter publishes is a Prometheus **gauge**,
prefixed with `teamspeak_` and carrying exactly one label, `virtualserver_name`.
The exporter's own health is reported separately under `teamspeak_exporter_`
(see below).

The values are TeamSpeak's own numbers, passed through unconverted. A metric
means whatever the identically named field of TeamSpeak's `serverinfo` response
means — this exporter does not reinterpret, scale, or rate them. Counters that
TeamSpeak reports as totals are still exposed as gauges; that is existing
behavior and changing it would break existing dashboards.

## Stability

**These names are public API.** Users' Grafana dashboards and Prometheus
alerting rules are keyed on them. They MUST NOT be renamed, removed, re-prefixed
or re-labelled as a side effect of another change. See AGENTS.md, "The Metric
Contract".

Adding a metric that `serverinfo` already returns is a small allowed change —
add the name to `METRICS_NAMES` in `app.py`, add it to this table, and add a
test.

## The 41 metrics

| Prometheus metric | TeamSpeak `serverinfo` field |
| --- | --- |
| `teamspeak_connection_bandwidth_received_last_minute_total` | `connection_bandwidth_received_last_minute_total` |
| `teamspeak_connection_bandwidth_received_last_second_total` | `connection_bandwidth_received_last_second_total` |
| `teamspeak_connection_bandwidth_sent_last_minute_total` | `connection_bandwidth_sent_last_minute_total` |
| `teamspeak_connection_bandwidth_sent_last_second_total` | `connection_bandwidth_sent_last_second_total` |
| `teamspeak_connection_bytes_received_control` | `connection_bytes_received_control` |
| `teamspeak_connection_bytes_received_keepalive` | `connection_bytes_received_keepalive` |
| `teamspeak_connection_bytes_received_speech` | `connection_bytes_received_speech` |
| `teamspeak_connection_bytes_received_total` | `connection_bytes_received_total` |
| `teamspeak_connection_bytes_sent_control` | `connection_bytes_sent_control` |
| `teamspeak_connection_bytes_sent_keepalive` | `connection_bytes_sent_keepalive` |
| `teamspeak_connection_bytes_sent_speech` | `connection_bytes_sent_speech` |
| `teamspeak_connection_bytes_sent_total` | `connection_bytes_sent_total` |
| `teamspeak_connection_filetransfer_bandwidth_received` | `connection_filetransfer_bandwidth_received` |
| `teamspeak_connection_filetransfer_bandwidth_sent` | `connection_filetransfer_bandwidth_sent` |
| `teamspeak_connection_filetransfer_bytes_received_total` | `connection_filetransfer_bytes_received_total` |
| `teamspeak_connection_filetransfer_bytes_sent_total` | `connection_filetransfer_bytes_sent_total` |
| `teamspeak_connection_packets_received_control` | `connection_packets_received_control` |
| `teamspeak_connection_packets_received_keepalive` | `connection_packets_received_keepalive` |
| `teamspeak_connection_packets_received_speech` | `connection_packets_received_speech` |
| `teamspeak_connection_packets_received_total` | `connection_packets_received_total` |
| `teamspeak_connection_packets_sent_control` | `connection_packets_sent_control` |
| `teamspeak_connection_packets_sent_keepalive` | `connection_packets_sent_keepalive` |
| `teamspeak_connection_packets_sent_speech` | `connection_packets_sent_speech` |
| `teamspeak_connection_packets_sent_total` | `connection_packets_sent_total` |
| `teamspeak_virtualserver_channelsonline` | `virtualserver_channelsonline` |
| `teamspeak_virtualserver_client_connections` | `virtualserver_client_connections` |
| `teamspeak_virtualserver_clientsonline` | `virtualserver_clientsonline` |
| `teamspeak_virtualserver_maxclients` | `virtualserver_maxclients` |
| `teamspeak_virtualserver_month_bytes_downloaded` | `virtualserver_month_bytes_downloaded` |
| `teamspeak_virtualserver_month_bytes_uploaded` | `virtualserver_month_bytes_uploaded` |
| `teamspeak_virtualserver_query_client_connections` | `virtualserver_query_client_connections` |
| `teamspeak_virtualserver_queryclientsonline` | `virtualserver_queryclientsonline` |
| `teamspeak_virtualserver_reserved_slots` | `virtualserver_reserved_slots` |
| `teamspeak_virtualserver_total_bytes_downloaded` | `virtualserver_total_bytes_downloaded` |
| `teamspeak_virtualserver_total_bytes_uploaded` | `virtualserver_total_bytes_uploaded` |
| `teamspeak_virtualserver_total_packetloss_control` | `virtualserver_total_packetloss_control` |
| `teamspeak_virtualserver_total_packetloss_keepalive` | `virtualserver_total_packetloss_keepalive` |
| `teamspeak_virtualserver_total_packetloss_speech` | `virtualserver_total_packetloss_speech` |
| `teamspeak_virtualserver_total_packetloss_total` | `virtualserver_total_packetloss_total` |
| `teamspeak_virtualserver_total_ping` | `virtualserver_total_ping` |
| `teamspeak_virtualserver_uptime` | `virtualserver_uptime` |

## Scrape shape

One series per metric per virtualserver of the configured host:

```text
teamspeak_virtualserver_clientsonline{virtualserver_name="Test Server"} 12.0
teamspeak_virtualserver_clientsonline{virtualserver_name="Zweiter Server"} 3.0
```

Virtualservers are discovered on every poll via `serverlist`, so a newly created
one appears without a restart. A removed or stopped one — or one that could
not be read — disappears from the next poll's output. A stopped virtualserver
is a state of the host, not an exporter error: it does not affect
`teamspeak_exporter_poll_success`.

A virtualserver name that contains the ServerQuery password is exported with
the password replaced by `*censored*`.

Virtualservers with identical names write the same series; only the last one
read is exported, and the exporter logs a warning. Give them distinct names.

If a `serverinfo` field is missing or not a number, only that series is left
out; the rest of the virtualserver is still exported and
`teamspeak_exporter_missing_fields` counts the gap.

## Exporter metrics

The exporter's own health. Part of the same stability contract.

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `teamspeak_exporter_last_poll_timestamp_seconds` | gauge | | Unix time the last poll started |
| `teamspeak_exporter_last_successful_poll_timestamp_seconds` | gauge | | Unix time the last fully successful poll started; 0 until one succeeds |
| `teamspeak_exporter_poll_success` | gauge | | 1 if the last poll read every virtualserver without error, else 0 |
| `teamspeak_exporter_poll_duration_seconds` | gauge | | Duration of the last poll |
| `teamspeak_exporter_poll_errors_total` | counter | `reason` | Failed polls or poll steps: `connection` (unreachable, dropped, banned IP), `login` (credentials rejected), `query` (any other ServerQuery error), `unexpected` |
| `teamspeak_exporter_missing_fields` | gauge | `virtualserver_name` | Contract fields missing or not numeric in the last `serverinfo` |
| `teamspeak_exporter_build_info` | gauge (info) | `version` | Always 1 |

Useful alerts:

```yaml
- alert: TeamSpeakExporterStale
  expr: time() - teamspeak_exporter_last_successful_poll_timestamp_seconds > 120
- alert: TeamSpeakMissingMetrics
  expr: teamspeak_exporter_missing_fields > 0
```

## What is not exported

Per-client, per-channel, and per-group data. The exporter issues no ServerQuery
command that returns them, by design (AGENTS.md, "What This Project Is Not").
