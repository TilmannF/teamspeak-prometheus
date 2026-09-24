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
the usual port 8000; pass extra flags through `RUN_FAKE_ARGS`:

```bash
make run-fake RUN_FAKE_ARGS="--metricsport 8231"
make run-fake RUN_FAKE_ARGS="--iterations 2 --interval 0.2"   # what CI runs
make run-fake RUN_FAKE_ARGS="--virtualservers 6 --flood-limit 10"
```

## Layout

| File | Purpose |
| --- | --- |
| `tests/fixtures/ts3-3.13.8-*.bin` | Raw bytes captured from a real TeamSpeak 3.13.8 server |
| `tests/serverquery.py` | Independent reference encoder: escaping, framing, records |
| `tests/fake_ts3_server.py` | Threaded TCP ServerQuery stub: N virtualservers, flood protection |
| `tests/exporter_harness.py` | Runs the real exporter against the fake server |
| `tests/fakes.py` | In-process fake client and scripted connection — no sockets |
| `tests/test_serverquery.py` | Reference encoder round-trips |
| `tests/test_client.py` | `ServerQueryClient` against scripted bytes and the real captures |
| `tests/test_config.py` | Defaults, environment precedence, validation, override warnings |
| `tests/test_metrics.py` | The metric contract: names, prefix, label, values, self-metrics |
| `tests/test_service.py` | Poll sequence, error survival, series lifecycle, backoff |
| `tests/test_smoke.py` | Subprocess boot → scrape `/metrics`, flood and outage survival |

## Markers

Unit tests must not open a socket, spawn a process, or sleep on the clock.
Anything that does is marked `smoke` and excluded from `make test`:

```python
pytestmark = pytest.mark.smoke
```

## Keeping the fake honest

The production client is tested against three independent things:

* **Real captures.** `tests/fixtures/` holds the banner, a `serverlist` and a
  `serverinfo` response recorded byte-for-byte from TeamSpeak 3.13.8 (the
  unique identifier replaced by a fake one). `tests/test_client.py` parses them.
* **A reference encoder.** `tests/serverquery.py` is a separate implementation
  of the escaping rules; the fake server uses it and the client tests compare
  against it.
* **The fake server.** Two-line banner, `\n\r` framing, error trailers and
  TeamSpeak-style flood protection, over real TCP.

To refresh the captures, run the official image and record the raw responses:

```bash
docker run -d --name ts3 -p 127.0.0.1:10011:10011 \
  -e TS3SERVER_LICENSE=accept \
  -e TS3SERVER_SERVERADMIN_PASSWORD=<pick one> teamspeak:latest
```

Replace `virtualserver_unique_identifier` before committing.

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

with FakeTs3Server(password='fake-password', virtualserver_count=3) as server:
    ...  # server.host, server.port
```

It binds an ephemeral port, so tests never collide.

`fake-password` is the password the fake server accepts by default, both in that
constructor and when running it standalone with `python -m tests.fake_ts3_server`.
It is documented here rather than printed by the program, so that nothing in the
tree prints a password — see AGENTS.md, "Secrets".
