# Testing

**No TeamSpeak server is required to develop or verify anything here.**

```bash
make setup        # .venv with runtime + dev dependencies
make check        # ruff check, ruff format --check, unit tests
make test-smoke   # end-to-end against the fake ServerQuery server
make run-fake     # run the exporter locally and scrape it by hand
```

`make run-fake` starts a fake TeamSpeak ServerQuery interface and the real
exporter against it, then prints the `curl` command to scrape. It listens on
the usual port 8000; pass extra flags through `RUN_FAKE_ARGS` if that port is
taken or you want it to stop by itself:

```bash
make run-fake RUN_FAKE_ARGS="--metricsport 8231"
make run-fake RUN_FAKE_ARGS="--iterations 2 --interval 0.2"   # what CI runs
```

## Layout

| File | Purpose |
| --- | --- |
| `tests/serverquery.py` | Wire helpers: escaping, `\n\r` framing, record encoding |
| `tests/fake_ts3_server.py` | Threaded TCP ServerQuery stub with canned virtualservers |
| `tests/query_client.py` | Socket client used by the harness (see below) |
| `tests/exporter_harness.py` | Runs the real exporter against the fake server |
| `tests/fakes.py` | In-process fake client for unit tests — no sockets |
| `tests/test_config.py` | Defaults, environment precedence, port parsing |
| `tests/test_metrics.py` | The metric contract: names, prefix, label, values |
| `tests/test_service.py` | Login, the poll sequence, error paths |
| `tests/test_smoke.py` | Subprocess boot → scrape `/metrics` |
| `tests/test_ts3_wire.py` | The real `ts3` package against the fake server |

## Markers

Unit tests must not open a socket, spawn a process, or sleep on the clock.
Anything that does is marked `smoke` and excluded from `make test`:

```python
pytestmark = pytest.mark.smoke
```

## Why there are two clients

The production exporter uses the archived `ts3` package. That package imports
`telnetlib`, which was **removed in Python 3.13**, so it cannot even be
installed on a modern interpreter.

To keep the suite runnable everywhere:

* `tests/query_client.py` speaks the same ServerQuery protocol and is what the
  smoke harness injects. It runs on any Python version.
* `tests/test_ts3_wire.py` drives the fake server with the *real* `ts3` package
  and is skipped when that package is missing. It exists so the fake cannot
  quietly drift away from the protocol the production code actually speaks.

CI runs unit tests on 3.12/3.13/3.14 and the smoke tests on 3.12, where `ts3`
installs. Replacing the library is the first item in
[modernization-backlog.md](modernization-backlog.md).

## Adding a metric

1. Add the field name to `METRICS_NAMES` in `app.py`.
2. Add the row to [metrics.md](metrics.md).
3. `tests/test_metrics.py::test_every_documented_metric_gets_a_gauge` asserts the
   count — update it.
4. The fake server derives its payload from `METRICS_NAMES`, so it needs no
   change.

## Writing a test that needs a server

```python
from tests.fake_ts3_server import FakeTs3Server

with FakeTs3Server(password='fake-password') as server:
    ...  # server.host, server.port
```

It binds an ephemeral port, so tests never collide.
