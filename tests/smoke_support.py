"""Shared helpers of the smoke tests: processes, ports, scraping.

Spawn the real exporter, the harness and the fake server; not collected
by pytest itself. The fixtures built on them live in conftest.py.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import time

import requests

from tests.fake_ts3_server import virtualservers

PASSWORD = 'smoke-test-password'


# Scrapes go to 127.0.0.1 directly. requests honours proxy variables from the
# environment, so a developer's HTTP_PROXY would otherwise break every test here.
HTTP = requests.Session()


HTTP.trust_env = False


CONNECTION_ERRORS = re.compile(
    r'^teamspeak_exporter_poll_errors_total\{reason="connection"\} [1-9]', re.M
)


FORGED = '2026-09-25 12:00:00,000 CRITICAL forged'


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def start_exporter(*extra: str) -> tuple[subprocess.Popen[str], int]:
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            '-u',  # unbuffered, so output survives terminate()
            '-m',
            'tests.exporter_harness',
            '--metricsport',
            str(port),
            '--ts3password',
            PASSWORD,
            '--interval',
            '0.2',
            *extra,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return process, port


def stop(process: subprocess.Popen[str]) -> str:
    process.terminate()
    try:
        return process.communicate(timeout=10)[0]
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate()[0]


def scrape(port: int, names: list[str], timeout: float = 20.0) -> str:
    """Scrape until every named virtualserver has landed.

    The exporter updates gauges one virtualserver at a time, so a body can
    contain the first virtualserver and not yet the second. Waiting for every
    expected one is what makes this deterministic.
    """

    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            body = HTTP.get(f'http://127.0.0.1:{port}/metrics', timeout=2).text
            if all(
                f'teamspeak_virtualserver_uptime{{virtualserver_name="{name}"}}' in body
                for name in names
            ):
                return body
        except requests.RequestException as err:  # server not up yet
            last_error = err
        time.sleep(0.2)
    raise AssertionError(
        f'exporter never served a complete cycle for {names} (last error: {last_error})'
    )


def names(count: int = 2) -> list[str]:
    return [str(server['virtualserver_name']) for server in virtualservers(count)]


def app_env(**env: str) -> dict[str, str]:
    """A minimal environment for ``python app.py``: nothing inherited that
    could point the exporter somewhere else."""

    return {'PATH': os.environ.get('PATH', ''), **env}


def run_app(
    env: dict[str, str], until: re.Pattern[str], argv: tuple[str, ...] = ()
) -> str:
    """Run the real entry point, ``python app.py``, until ``until`` shows up in
    a scrape; return everything it logged."""

    return scrape_app(env, until, argv)[0]


def scrape_app(
    env: dict[str, str], until: re.Pattern[str], argv: tuple[str, ...] = ()
) -> tuple[str, str]:
    """Like ``run_app``, and also return the scrape that matched."""

    port = free_port()
    process = subprocess.Popen(
        [sys.executable, '-u', 'app.py', *argv],
        env=app_env(METRICS_PORT=str(port), TEAMSPEAK_POLL_INTERVAL='1', **env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    body = ''
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                body = HTTP.get(f'http://127.0.0.1:{port}/metrics', timeout=2).text
                if until.search(body):
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        else:
            raise AssertionError(f'never saw {until.pattern}')
    finally:
        output = stop(process)
    return output, body


def wait_for_metrics(port: int, timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            HTTP.get(f'http://127.0.0.1:{port}/metrics', timeout=2)
            return
        except requests.RequestException:
            time.sleep(0.1)
    raise AssertionError('metrics endpoint never came up')


def terminate_and_time(process: subprocess.Popen[str]) -> tuple[float, str]:
    started = time.monotonic()
    process.send_signal(signal.SIGTERM)
    output = process.communicate(timeout=10)[0]
    return time.monotonic() - started, output


def run_tool(*argv: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-u', '-m', *argv],
        env=app_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_briefly(argv: list[str], env: dict[str, str], seconds: float = 3) -> str:
    """Run a command for up to ``seconds``, then SIGTERM it; return its output."""

    process = subprocess.Popen(
        [sys.executable, '-u', *argv],
        env=app_env(**env),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        return process.communicate(timeout=seconds)[0]
    except subprocess.TimeoutExpired:
        process.send_signal(signal.SIGTERM)
        return process.communicate(timeout=10)[0]


def non_loopback_address() -> str | None:
    """An address of this machine other than 127.0.0.1, if it has one."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('192.0.2.1', 9))  # TEST-NET-1; nothing is sent
        except OSError:
            return None
        address = probe.getsockname()[0]
    return None if address.startswith('127.') else address
