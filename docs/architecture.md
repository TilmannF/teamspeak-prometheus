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
raises `ServerQueryError`. Only a credential rejection on `login` — error 520,
"invalid loginname or password" — raises `LoginFailed`; a login that fails for
any other reason (still flooding after all retries, a ban, a missing
permission) stays a `ServerQueryError`, so it is not reported as a bad
password. Error messages name the command, never its parameters.

TeamSpeak throttles query clients whose IP is not in `query_ip_allowlist.txt`
(default: 10 commands per 3 seconds) and answers `error id=524 ... please wait
N seconds`. The client waits as told (at most 10s) and retries the command up
to three times. A poll costs `3 + 2 × virtualservers` commands, so with four or
more virtualservers a non-allowlisted exporter is throttled every poll; before
this handling existed, it silently skipped the rest of the virtualservers.
Flooding on regardless gets the IP banned, after which TeamSpeak closes new
connections without a greeting.

Every read has a 10s socket timeout, and a whole session has a 60s deadline
(`POLL_TIMEOUT_IN_SECONDS`): a server trickling bytes or sending notifications
without end cannot stall a poll, because each read is bounded by the time left.
A flood wait that would cross the deadline is not slept. Lines longer than 1 MiB
(`MAX_LINE_BYTES`) are rejected. An `error` trailer without a numeric id is a
protocol error, not success.

## Error behavior

The process only exits for invalid configuration or a metrics port it cannot
bind. Everything else is logged, counted, and retried:

| Situation | Behavior | Counted as |
| --- | --- | --- |
| Connection refused, dropped, timed out, IP banned | poll fails, backoff | `reason="connection"` |
| Session exceeds 60s, or a line exceeds 1 MiB | poll fails, backoff | `reason="connection"` |
| `error` trailer without a numeric id | poll or step fails | `reason="query"` |
| Login rejected: wrong credentials (error 520) | poll fails, backoff | `reason="login"` |
| Login fails otherwise (flooding, ban, permission) | poll fails, backoff | `reason="query"` |
| `serverlist` returns an error | poll fails, backoff | `reason="query"` |
| A virtualserver is not `online` in `serverlist` (stopped, booting, …) | skipped, not an error; logged once per status change | — |
| One online virtualserver fails (`use`/`serverinfo` error) | skipped, others still read, poll partial | `reason="query"` |
| `error id=524` flooding | wait and retry, up to 3 times | `reason="query"` if still failing |
| `serverinfo` field missing or not numeric | that series skipped and removed, warning logged once | `teamspeak_exporter_missing_fields` |
| Anything else | logged with traceback, poll fails, backoff | `reason="unexpected"` |

A poll that **fails** keeps every existing series at its last value and sets
`teamspeak_exporter_poll_success` to 0 — alert on
`teamspeak_exporter_last_successful_poll_timestamp_seconds` to catch stale
data. Backoff doubles the wait per consecutive failed poll, capped at
`max(60s, poll interval)`, and resets on the next poll that reaches the server.
The poll interval itself is 1 to 86400 seconds: below 1s the exporter would
hammer ServerQuery. Even inside that range, `3 + 2 × virtualservers` commands
per poll against TeamSpeak's default of 10 per 3 seconds means a
non-allowlisted exporter with one virtualserver is throttled below 1.5s, and
earlier with more.
A **partial** poll does not back off.

## Untrusted server text: logs and labels

Error messages and virtualserver names come from the TeamSpeak server and are
logged as arguments of log calls. They are untrusted: a hostile or compromised
server could echo the password it was just sent, or embed line breaks to forge
log lines. `main()` installs a `RedactingFilter` on the exporter's logger,
which on every record

* replaces the configured password with `*censored*` — in the message, its
  arguments, and any traceback;
* escapes control characters (line breaks, terminal escapes, C1 codes, Unicode
  line separators) in string arguments, so one log call is always one line.

The message templates are the exporter's own text and are left as they are; the
multi-line settings banner stays readable. Numbers pass through untouched.

The same applies to metrics: `virtualserver_name` label values come from the
server too, and `/metrics` is unauthenticated and scraped into long-term
storage. The service passes every server-supplied name through `redact()` —
the function the filter uses — before it becomes a label, so a virtualserver
named after the password is exported as `*censored*`.

A very short password is replaced wherever it appears, also inside unrelated
text. That over-censors, but never leaks.

## Series lifecycle

After every poll that got a `serverlist`, the series of virtualservers that
were not read are removed: deleted ones, ones not `online`, and ones that failed
this poll. Virtualservers sharing a name share their series — the last one
read wins — and are warned about once per name. A
missing `serverinfo` field removes just that series.

## Container healthcheck

Docker runs `HEALTHCHECK` as a separate process in the container. It inherits
the container's environment, but not the exporter's command-line flags, so
probing `METRICS_PORT` (or 8000) alone would declare an exporter started with
`--metricsport` permanently unhealthy.

`healthcheck.py` closes that gap without duplicating any configuration logic:

```text
exporter_argv(/proc)        every /proc/<pid>/cmdline, lowest PID first,
                            skipping itself; the arguments after app.py
metrics_port(argv, env)     app.parse_args + app.resolve_config:
                            the exporter's own precedence rules
probe(port)                 GET http://127.0.0.1:<port>/metrics, 4s timeout,
                            never through a proxy
```

* All processes are searched, not only PID 1, so `docker run --init` and
  `sh -c` wrappers work. A shell's `-c` string is one argument and does not
  match; its child does.
* Without an exporter process (a replaced container command) the port comes
  from the environment alone, like an exporter started without flags.
* The exporter's command line may contain `--ts3password`. argparse error
  output is swallowed and error messages never repeat an argument, so the
  healthcheck output — stored by Docker and visible via `docker inspect` —
  cannot contain it.
* Proxy variables (`http_proxy`, `HTTP_PROXY`, `all_proxy`) are ignored. The
  healthcheck inherits the container environment, Docker can inject proxy
  settings into every container, and urllib would otherwise send the loopback
  request to the proxy unless `NO_PROXY` lists 127.0.0.1 — reporting a working
  exporter as unhealthy.
* The healthcheck runs as the same non-root user as the exporter, which is what
  allows it to read the exporter's `/proc` entry.
* Healthy is deliberately independent of TeamSpeak. An orchestrator that
  restarts unhealthy containers must not restart the exporter because
  TeamSpeak is down; `teamspeak_exporter_poll_success` reports that.

## Shutdown

`main()` installs a SIGTERM handler that raises `Shutdown`, a `BaseException`
like `KeyboardInterrupt`, so `poll()`'s `except Exception` cannot swallow it.
It interrupts the exporter wherever it is — sleeping between polls or blocked
in a socket read — the `finally` blocks close the ServerQuery session, and the
process logs `Stopped` and exits 0.

This matters in the container, where the exporter is PID 1: the kernel ignores
signals PID 1 has no handler for, so without it `docker stop` would wait 10
seconds and SIGKILL the process (exit code 137).

## Configuration precedence

Environment variables win over command-line arguments. That is long-standing
behavior and is relied on by the Docker usage in the README, so it is preserved
exactly. Since 1.0.0 the exporter logs a warning naming each flag that an
environment variable overrides with a different value.

argparse reads every flag as a plain string and validates nothing. Each option
has one parser in `_OPTIONS`, used by `resolve_config` for whichever value wins
— so a malformed flag that an environment variable overrides is ignored with
that warning instead of stopping the exporter, and an error for a value that is
used names both spellings: `TEAMSPEAK_PORT (--ts3port) must be a port number`.

## Startup and secrets

`main()` installs the `RedactingFilter` before it parses or validates anything:
first with `TEAMSPEAK_PASSWORD`, then with every password given — flag and
environment variable, used or overridden. A configuration error that quotes an
invalid value equal to the password therefore prints `*censored*`.

argparse prints its own errors to stderr, outside logging.
`SafeArgumentParser` keeps only the option strings it defines visible
(`--ts3host`, `--help`, …) and replaces every other command-line token with
`…`, as well as anything after `=`. Unknown tokens starting with `-` are masked
too: a mistyped `--ts3pasword` and a password `-secret` cannot be told apart.
So `--ts3pasword <password>` reports
`unrecognized arguments: … … (argument values hidden; see --help)`, while
`argument --ts3port: expected one argument` stays readable. Only whole
fragments are masked; a password `3` leaves `--ts3port` intact. The test harness and the fake server use
it too, and a test scans the repository for any parser that does not.
