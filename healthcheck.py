"""Container healthcheck for teamspeak-prometheus.

Docker runs ``HEALTHCHECK`` as a separate process. It inherits the container's
environment variables but not the exporter's command-line flags, so a probe of
``METRICS_PORT`` (or 8000) misses an exporter started with ``--metricsport``.

This script finds the running exporter in ``/proc``, reads its arguments, and
resolves the metrics port with the exporter's own ``parse_args`` and
``resolve_config`` -- same precedence rules, no duplicated logic. Then it
requests ``/metrics`` on that port, bypassing any proxy configured in the
environment.

Healthy (exit 0) means the metrics endpoint answers. Whether TeamSpeak is
reachable is deliberately not part of it: that is what
``teamspeak_exporter_poll_success`` reports, and an unreachable TeamSpeak must
not get the exporter restarted.

The exporter's command line may contain ``--ts3password``. Nothing read from
it is ever printed.
"""

from __future__ import annotations

import contextlib
import io
import os
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path

import app

PROC = Path('/proc')
EXPORTER_SCRIPT = 'app.py'
PROBE_TIMEOUT_IN_SECONDS = 4.0


class HealthcheckError(Exception):
    """The exporter's metrics endpoint could not be located or reached."""


def exporter_argv(proc: Path = PROC, own_pid: int | None = None) -> list[str] | None:
    """Arguments after ``app.py`` of the running exporter, or ``None``.

    Every process is searched, not just PID 1: with ``docker run --init`` or a
    ``sh -c`` wrapper the exporter is a child process. If several match, the
    lowest PID wins.
    """

    own_pid = os.getpid() if own_pid is None else own_pid
    try:
        entries = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:  # no procfs, e.g. outside Linux
        return None
    for entry in sorted(entries, key=lambda e: int(e.name)):
        if int(entry.name) == own_pid:
            continue
        try:
            raw = (entry / 'cmdline').read_bytes()
        except OSError:  # exited meanwhile, a kernel thread, or not ours
            continue
        args = raw.decode('utf-8', errors='replace').split('\0')
        if args and args[-1] == '':
            args.pop()
        for index, arg in enumerate(args):
            if Path(arg).name == EXPORTER_SCRIPT:
                return args[index + 1 :]
    return None


def metrics_port(argv: list[str], env: Mapping[str, str]) -> int:
    """The port the exporter listens on, resolved exactly as the exporter does.

    Raises ``HealthcheckError`` without repeating any argument: the command line
    may carry the ServerQuery password.
    """

    try:
        # argparse writes errors, quoting the offending values, to stderr and
        # help to stdout; neither may reach the healthcheck output.
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            args = app.parse_args(argv)
    except SystemExit:
        raise HealthcheckError('the exporter command line is not valid') from None
    try:
        return app.resolve_config(args, env).metrics_port
    except app.ExporterError:
        raise HealthcheckError('the exporter configuration is not valid') from None


def probe(port: int) -> None:
    """Request ``/metrics`` on the loopback interface, never through a proxy.

    urllib honours ``http_proxy``/``HTTP_PROXY``/``all_proxy`` from the
    environment and exempts 127.0.0.1 only when ``NO_PROXY`` says so. The
    healthcheck inherits the container's environment -- Docker can inject proxy
    variables into every container -- and a proxy cannot reach this
    container's loopback anyway, so proxies are disabled outright.
    """

    url = f'http://127.0.0.1:{port}/metrics'
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direct.open(url, timeout=PROBE_TIMEOUT_IN_SECONDS):
            pass
    except OSError as err:
        raise HealthcheckError(f'{url} did not answer: {err}') from err


def check(
    env: Mapping[str, str],
    proc: Path = PROC,
    prober: Callable[[int], None] = probe,
) -> int:
    """Locate the exporter, probe its endpoint, and return the port probed.

    Without a running exporter in ``proc`` (for example a replaced container
    command), the port is resolved from the environment alone -- the same as
    an exporter started without flags.
    """

    argv = exporter_argv(proc)
    port = metrics_port(argv or [], env)
    prober(port)
    return port


def main() -> int:
    try:
        port = check(os.environ)
    except HealthcheckError as err:
        print(f'unhealthy: {err}')
        return 1
    print(f'healthy: metrics endpoint on port {port} answers')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
