"""End-to-end: boot the exporter against the fake TS3 server and scrape it.

No TeamSpeak server and no ``ts3`` package are needed. Marked ``smoke`` because
it spawns a subprocess and opens sockets.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time

import pytest
import requests

from tests.fake_ts3_server import VIRTUALSERVERS

pytestmark = pytest.mark.smoke

PASSWORD = 'smoke-test-password'


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


@pytest.fixture
def exporter():
    port = free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            '-u',  # unbuffered, so stdout survives terminate()
            '-m',
            'tests.exporter_harness',
            '--metricsport',
            str(port),
            '--ts3password',
            PASSWORD,
            '--interval',
            '0.2',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        yield process, port
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def scrape(port: int, timeout: float = 20.0) -> str:
    """Scrape until a full poll cycle has landed.

    The exporter updates gauges one virtualserver at a time, so a body can
    contain the first virtualserver and not yet the second. Waiting for every
    known virtualserver is what makes this deterministic -- waiting for any
    single sample races the poll loop.
    """

    expected = [server['virtualserver_name'] for server in VIRTUALSERVERS]
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    body = ''
    while time.monotonic() < deadline:
        try:
            body = requests.get('http://127.0.0.1:%d/metrics' % port, timeout=2).text
            if all(
                'teamspeak_virtualserver_uptime{virtualserver_name="%s"}' % name in body
                for name in expected
            ):
                return body
        except requests.RequestException as err:  # server not up yet
            last_error = err
        time.sleep(0.2)
    raise AssertionError(
        'exporter never served a complete cycle for %s (last error: %s)'
        % (expected, last_error)
    )


def test_the_exporter_serves_teamspeak_metrics(exporter):
    _, port = exporter

    body = scrape(port)

    assert (
        'teamspeak_virtualserver_clientsonline{virtualserver_name="Test Server"}'
        in body
    )
    assert 'teamspeak_virtualserver_uptime{virtualserver_name="Zweiter Server"}' in body


def test_every_metric_family_is_exposed(exporter):
    import app

    _, port = exporter

    body = scrape(port)

    exposed = {
        line.split('{')[0]
        for line in body.splitlines()
        if line.startswith('teamspeak_')
    }
    assert exposed == {'teamspeak_' + name for name in app.METRICS_NAMES}


def test_the_password_is_never_printed(exporter):
    process, port = exporter
    scrape(port)

    process.terminate()
    output = process.communicate(timeout=10)[0]

    assert PASSWORD not in output
    assert '*censored*' in output
