# Architecture

One module, `app.py`, plus a test suite. That is deliberate — the exporter is
small enough that a package layout would cost more than it buys.

## Flow

```text
main(argv)
  parse_args(argv)                     argparse, unchanged flags
  resolve_config(args, os.environ)     pure: -> Config (env wins over flags)
  describe_settings(config)            banner, password censored
  build_gauges(REGISTRY)               41 gauges, one label: virtualserver_name
  start_http_server(metrics_port)      prometheus_client, own thread
  poll_forever(service)
      every READ_INTERVAL_IN_SECONDS (5s):
          service.connect()            client_factory(host, port) + login
          service.read()               serverlist -> use -> serverinfo -> update_gauges
          service.disconnect()
```

## Boundaries

| Piece | Kind | Tested by |
| --- | --- | --- |
| `resolve_config` | pure function | `tests/test_config.py` |
| `build_gauges` / `update_gauges` | pure over an injected registry | `tests/test_metrics.py` |
| `Teamspeak3MetricService` | I/O, but the client is injected | `tests/test_service.py` |
| `poll_forever` | loop, bounded by `iterations` in tests | `tests/test_service.py` |
| `default_client_factory` | the only place that touches `ts3` | `tests/test_ts3_wire.py` |

Three properties make this testable, and all three are required by
`policies/20-python-code-policy.md`:

1. **No import-time work.** Everything runs behind `main()` and the `__main__`
   guard, so tests can `import app` freely.
2. **An injected client.** `Teamspeak3MetricService` takes a `client_factory`.
   Unit tests pass an in-process fake, so they need neither a socket nor the
   `ts3` package.
3. **An explicit registry.** `build_gauges` takes a `CollectorRegistry`. The
   global default registry raises `Duplicated timeseries` the second time gauges
   are built in one process, which would make the suite unrunnable.

## Error behavior

| Situation | Today's behavior |
| --- | --- |
| Login rejected | `LoginFailed` raised; the process exits |
| `serverlist` returns an error | logged, cycle skipped, loop continues |
| `serverinfo` returns an error | logged, rest of the cycle skipped, loop continues |
| Connection refused / dropped | raised by the client; the process exits |
| `serverinfo` missing an expected field | `KeyError`; the process exits |

The process is expected to be restarted by Docker or the init system. Retry and
backoff are a backlog item, not current behavior.

## Configuration precedence

Environment variables win over command-line arguments. That is long-standing
behavior and is relied on by the Docker usage in the README, so it is preserved
exactly.
