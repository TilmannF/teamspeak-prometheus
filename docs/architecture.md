# Architecture

One module, `app.py`, plus a test suite. That is deliberate — the exporter is
small enough that a package layout would cost more than it buys.

## Flow

```text
main(argv)
  parse_args(argv)                     argparse; unset flags stay None
  resolve_config(args, os.environ)     pure: -> Config (env wins over flags)
  overridden_flags(args, os.environ)   warn about flags an env var overrides
  describe_settings(config)            banner, password censored
  build_gauges(REGISTRY)               41 gauges, one label: virtualserver_name
  build_exporter_metrics(REGISTRY)     teamspeak_exporter_* self-metrics
  start_http_server(metrics_port)      prometheus_client, own thread
  poll_forever(service, interval)
      every poll_interval (start to start), longer after failures:
          service.poll()               never raises
              ServerQueryClient.connect(host, port)   10s socket timeout
              login -> serverlist -> (use -> serverinfo) per virtualserver
              update_gauges()          pure; skips missing / non-numeric fields
              forget removed virtualservers
              close (quit)
```

## Boundaries

| Piece | Kind | Tested by |
| --- | --- | --- |
| `resolve_config`, `overridden_flags` | pure functions | `tests/test_config.py` |
| `build_gauges` / `update_gauges` | pure over an injected registry | `tests/test_metrics.py` |
| `ServerQueryClient` | protocol over an injected connection | `tests/test_client.py` (scripted bytes, real TS3 captures) |
| `Teamspeak3MetricService` | I/O, but the client is injected | `tests/test_service.py` |
| `poll_forever`, `next_delay` | loop over injected clock and sleep | `tests/test_service.py` |
| everything together | subprocess + fake TCP server | `tests/test_smoke.py` |

Three properties make this testable, and all three are required by
`policies/20-python-code-policy.md`:

1. **No import-time work.** Everything runs behind `main()` and the `__main__`
   guard, so tests can `import app` freely.
2. **Injected I/O.** `Teamspeak3MetricService` takes a `client_factory` and a
   clock; `ServerQueryClient` takes a connection and a sleep function;
   `poll_forever` takes a clock and a sleep function. Unit tests never open a
   socket or wait.
3. **An explicit registry.** `build_gauges` takes a `CollectorRegistry`. The
   global default registry raises `Duplicated timeseries` the second time gauges
   are built in one process, which would make the suite unrunnable.

## The ServerQuery client

`ServerQueryClient` speaks the raw TeamSpeak 3 ServerQuery protocol (TCP 10011)
with the standard library only. It sends one command at a time and reads until
the `error id=... msg=...` trailer; `notify*` lines are skipped. A non-zero id
raises `ServerQueryError`, or `LoginFailed` for `login`. Error messages name the
command, never its parameters.

TeamSpeak throttles query clients whose IP is not in `query_ip_allowlist.txt`
(default: 10 commands per 3 seconds) and answers `error id=524 ... please wait
N seconds`. The client waits as told (at most 10s) and retries the command up
to three times. A poll costs `3 + 2 × virtualservers` commands, so with four or
more virtualservers a non-allowlisted exporter is throttled every poll; before
this handling existed, it silently skipped the rest of the virtualservers.
Flooding on regardless gets the IP banned, after which TeamSpeak closes new
connections without a greeting.

## Error behavior

The process only exits for invalid configuration or a metrics port it cannot
bind. Everything else is logged, counted, and retried:

| Situation | Behavior | Counted as |
| --- | --- | --- |
| Connection refused, dropped, timed out, IP banned | poll fails, backoff | `reason="connection"` |
| Login rejected | poll fails, backoff | `reason="login"` |
| `serverlist` returns an error | poll fails, backoff | `reason="query"` |
| One virtualserver fails (`use`/`serverinfo` error, e.g. stopped) | skipped, others still read, poll partial | `reason="query"` |
| `error id=524` flooding | wait and retry, up to 3 times | `reason="query"` if still failing |
| `serverinfo` field missing or not numeric | that series skipped and removed, warning logged once | `teamspeak_exporter_missing_fields` |
| Anything else | logged with traceback, poll fails, backoff | `reason="unexpected"` |

A poll that **fails** keeps every existing series at its last value and sets
`teamspeak_exporter_poll_success` to 0 — alert on
`teamspeak_exporter_last_successful_poll_timestamp_seconds` to catch stale
data. Backoff doubles the wait per consecutive failed poll, capped at
`max(60s, poll interval)`, and resets on the next poll that reaches the server.
A **partial** poll does not back off.

## Series lifecycle

After every poll that got a `serverlist`, the series of virtualservers that are
no longer listed (deleted, or skipped because they failed) are removed. A
missing `serverinfo` field removes just that series.

## Configuration precedence

Environment variables win over command-line arguments. That is long-standing
behavior and is relied on by the Docker usage in the README, so it is preserved
exactly. Since 1.0.0 the exporter logs a warning naming each flag that an
environment variable overrides with a different value.
