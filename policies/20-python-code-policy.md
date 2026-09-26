# Python Code Policy

This policy defines Python-specific rules for this repository. It overrides `00-engineering-policy.md` where they conflict.

Normative keywords follow `00-engineering-policy.md` §1.

## 1. Language Version

Target Python 3.12 or newer.

The container image pins the runtime; see `Dockerfile`.

Code MUST NOT use syntax or standard-library APIs unavailable in 3.12.

Code MUST NOT retain Python 2 compatibility shims.

## 2. Tooling

`ruff` is the only linter and formatter. Configuration lives in `pyproject.toml`.

Before finalizing a change, agents MUST run:

```bash
make check
```

which runs `ruff check`, `ruff format --check`, and the unit tests.

Agents MUST NOT claim these passed unless they were actually run.

Agents MUST NOT add a second formatter, linter, or type checker without an explicit task.

## 3. Module Structure

Modules MUST NOT perform work at import time.

Argument parsing, network connections, server startup, and loops MUST live inside functions, behind:

```python
if __name__ == '__main__':
    raise SystemExit(main())
```

Code lives in the package `teamspeak_prometheus`, one module per domain (see `docs/architecture.md`). `app.py` and `healthcheck.py` are entry points only: a docstring, one import of `main` from the package, and the `__main__` guard.

Every module MUST stay importable without side effects. The test suite imports them directly.

A module SHOULD stay under 300 lines and MUST NOT exceed 400; split it by responsibility before it does. This applies to test modules too. `tests/test_structure.py` enforces the hard limit for the package and `tests/`.

All logging MUST go through `teamspeak_prometheus.logs.log`. No module may create its own logger: the redaction filter sits on that one logger and does not reach child loggers.

## 4. Typing

Public functions MUST have type hints on parameters and return values.

Prefer built-in generics (`dict[str, Gauge]`) over `typing.Dict`.

Type hints are documentation, not enforcement — no type checker runs in CI today.

## 5. Configuration

Configuration MUST be represented by a frozen `dataclass`.

Configuration resolution MUST be a pure function of its inputs — it takes the parsed arguments and an environment mapping and returns a config object.

Core logic MUST NOT read `os.environ` directly. Pass the mapping in.

Invalid configuration MUST fail early with a clear error.

## 6. Dependency Injection

The TeamSpeak client MUST be injected into the metric service via constructor parameter, defaulting to the real client.

Unit tests MUST be able to run without opening a socket. The ServerQuery client takes its connection as a constructor parameter for that reason.

Core logic MUST NOT reach for module-level globals, clocks, or the network.

Prometheus gauges MUST be built against an explicitly passed `CollectorRegistry`. The default global registry raises `Duplicated timeseries` when a build runs twice in one process, which breaks tests.

## 7. Errors

Library code MUST raise explicit exceptions derived from `Exception`.

Library code MUST NOT call `exit()`, `sys.exit()`, or `raise` with no active exception.

Only `main()` may translate an exception into a process exit code.

Errors MUST NOT discard the root cause; use `raise ... from err` when re-raising.

## 8. Logging and Output

Production code MUST use `logging`, not `print`. Pass values as logging arguments (`log.info('x %s', y)`), not pre-formatted strings.

Neither `print` nor `logging` may ever emit the ServerQuery password. Error messages MUST name a failed command, never its parameters: `login` carries the password. The existing censoring in the settings banner MUST be preserved.

Values from the TeamSpeak server MUST be passed as logging arguments, never formatted into the message template: the `RedactingFilter` redacts the password everywhere, but escapes control characters only in arguments. Log calls SHOULD use `%s`, not `%r`, for server text; the filter has already escaped it.

## 9. Tests

Tests live in `tests/` and use `pytest`.

Test names MUST describe the behavior being verified.

Unit tests MUST NOT open sockets, sleep on wall-clock time, or depend on test order.

Tests that need a listening TCP socket or a subprocess MUST be marked `@pytest.mark.smoke` and MUST be excluded from `make test`.

Every behavior change MUST come with a test. A test that only restates the implementation is not a test.

Fixtures MUST be small and named after what they represent.

## 10. Dependencies

Runtime dependencies live in `requirements.txt`; development dependencies in `requirements-dev.txt`.

Runtime dependencies MUST be pinned or bounded. VCS dependencies MUST pin a commit SHA.

New dependencies MUST be justified in the change description. Prefer the standard library.

## 11. The Metric Contract

See `AGENTS.md`. Metric names, the `teamspeak_` prefix, and the `virtualserver_name` label are public API and MUST NOT change as a side effect of another task.
