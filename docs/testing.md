# Testing

**No TeamSpeak server is required to develop or verify anything here.**

```bash
make setup        # .venv with runtime + dev dependencies
make check        # ruff check, ruff format --check, unit tests
make test-smoke   # end-to-end against the fake ServerQuery server
make run-fake     # run the exporter locally and scrape it by hand
make docker-test  # build the image, test HEALTHCHECK and shutdown (needs Docker)
```

`make run-fake` starts a fake TeamSpeak ServerQuery interface and the real
exporter against it, then prints the `curl` command to scrape. Both listen on
127.0.0.1 only — a local test tool has no business on the network — with the
metrics on the usual port 8000; pass extra flags through `RUN_FAKE_ARGS`:

```bash
make run-fake RUN_FAKE_ARGS="--metricsport 8231"
make run-fake RUN_FAKE_ARGS="--iterations 2 --interval 0.2"   # what CI runs
# the harness hands --interval straight to the poll loop, so it may go below
# the exporter's 1-second minimum
make run-fake RUN_FAKE_ARGS="--virtualservers 6 --flood-limit 10"
```

## Layout

| File | Purpose |
| --- | --- |
| `tests/fixtures/ts3-3.13.8-*.bin` | Raw bytes captured from a real TeamSpeak 3.13.8 server |
| `tests/serverquery.py` | Independent reference encoder: escaping, framing, records |
| `tests/fake_ts3_server.py` | Threaded TCP ServerQuery stub: N virtualservers, flood protection, hostile mode |
| `tests/exporter_harness.py` | Runs the real exporter against the fake server |
| `tests/fakes.py` | In-process fake client and scripted connection — no sockets |
| `tests/test_serverquery.py` | Reference encoder round-trips |
| `tests/test_client.py` | `ServerQueryClient` against scripted bytes and the real captures |
| `tests/test_logging.py` | `RedactingFilter`: password censoring, forged-line escaping, a hostile server |
| `tests/test_cli.py` | Flags as strings, argparse error masking in the exporter and the test tools, `main()` never printing the password, a repository scan for unmasked parsers |
| `tests/test_release.py` | `__version__` and CHANGELOG agree; the release-tag script accepts only matching `vX.Y.Z`; in `release.yml`, only a validated tag push can log in, push, attest or release |
| `tests/test_config.py` | Defaults, environment precedence, validation, override warnings |
| `tests/test_metrics.py` | The metric contract: names, prefix, label, values, self-metrics |
| `tests/test_service.py` | Poll sequence, error survival, series lifecycle, backoff |
| `tests/test_smoke.py` | Subprocess boot → scrape `/metrics`, flood and outage survival, healthcheck probe, `python app.py` against a hostile server |
| `tests/test_healthcheck.py` | `healthcheck.py` against a fake `/proc`: finding the exporter, port resolution, no secret leaks |
| `tests/container_test.sh` | The built image: `HEALTHCHECK` in every port configuration and a clean `docker stop`, via `make docker-test` |

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
  TeamSpeak-style flood protection, over real TCP. `stopped={2}` reports a
  virtualserver `offline` and fails `use` on it with error 1033. With `hostile=True` it
  echoes the password and embeds forged log lines in its error text, and names
  a virtualserver after the password, to prove none of it reaches the log or
  `/metrics`.

To refresh the captures, run the official image and record the raw responses:

```bash
docker run -d --name ts3 -p 127.0.0.1:10011:10011 \
  -e TS3SERVER_LICENSE=accept \
  -e TS3SERVER_SERVERADMIN_PASSWORD=<pick one> teamspeak:latest
```

Replace `virtualserver_unique_identifier` before committing.

## The container test

`make docker-test` builds the image and starts it nine ways — default,
`METRICS_PORT`, `--metricsport` in the command, behind `--init`, behind
`sh -c`, env and flag both set, with an unreachable HTTP proxy in the
environment, with the password equal to the metrics port, and without an
exporter — then waits for Docker's verdict on each. All but the last must turn
healthy, the last unhealthy, and neither the health log nor the container log
may contain the password, not even where it equals the port. CI
runs it in the `docker` job. It takes about 20 seconds and needs no TeamSpeak
server.

It then stops three of them — default, behind `--init`, behind `sh -c` — with
`docker stop`, and requires exit code 0 within 5 seconds and `Stopped` in the
log. Without a SIGTERM handler the exporter, as PID 1, would ignore the signal
and be killed after Docker's 10-second grace period (exit code 137).

## No tool prints its password

`tests/test_smoke.py::test_no_tool_prints_its_password` runs every command-line
tool — the exporter, the healthcheck, the harness, the fake server — with
awkward passwords: equal to a port the tool prints, or starting with `-`, or
after a mistyped flag. No output may contain the password. **A new
command-line tool gets a row in `tool_cases()`.**

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
