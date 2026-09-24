"""End-to-end: boot the exporter against the fake TS3 server and scrape it.

No TeamSpeak server is needed. Marked ``smoke`` because it spawns a subprocess
and opens sockets.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time

import pytest
import requests

import app
import healthcheck
from tests.fake_ts3_server import FakeTs3Server, virtualservers

pytestmark = pytest.mark.smoke

PASSWORD = 'smoke-test-password'

# Scrapes go to 127.0.0.1 directly. requests honours proxy variables from the
# environment, so a developer's HTTP_PROXY would otherwise break every test here.
HTTP = requests.Session()
HTTP.trust_env = False
CONNECTION_ERRORS = re.compile(
    r'^teamspeak_exporter_poll_errors_total\{reason="connection"\} [1-9]', re.M
)


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


@pytest.fixture
def exporter():
    process, port = start_exporter()
    try:
        yield process, port
    finally:
        if process.poll() is None:
            stop(process)


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


def test_the_exporter_serves_teamspeak_metrics(exporter):
    _, port = exporter

    body = scrape(port, names())

    assert (
        'teamspeak_virtualserver_clientsonline{virtualserver_name="Test Server"}'
        in body
    )
    assert 'teamspeak_virtualserver_uptime{virtualserver_name="Zweiter Server"}' in body
    assert 'teamspeak_exporter_poll_success 1.0' in body


def test_every_metric_family_is_exposed(exporter):
    _, port = exporter

    body = scrape(port, names())

    exposed = {
        line.split('{')[0]
        for line in body.splitlines()
        if line.startswith('teamspeak_') and not line.startswith('teamspeak_exporter_')
    }
    assert exposed == {'teamspeak_' + name for name in app.METRICS_NAMES}


def test_the_password_is_never_printed(exporter):
    process, port = exporter
    scrape(port, names())

    output = stop(process)

    assert PASSWORD not in output
    assert '*censored*' in output


def test_many_virtualservers_survive_flood_protection():
    # 6 virtualservers need 15 commands per poll; the fake allows 10 per second,
    # like TeamSpeak's default for query clients not on its allowlist.
    process, port = start_exporter(
        '--virtualservers', '6', '--flood-limit', '10', '--flood-window', '1'
    )
    try:
        body = scrape(port, names(6), timeout=30)
    finally:
        stop(process)

    assert 'teamspeak_virtualserver_uptime{virtualserver_name="Server 6"}' in body


def test_an_unreachable_server_keeps_the_exporter_alive():
    process, port = start_exporter('--ts3port', str(free_port()))
    try:
        deadline = time.monotonic() + 20
        body = ''
        while time.monotonic() < deadline:
            try:
                body = HTTP.get(f'http://127.0.0.1:{port}/metrics', timeout=2).text
                if CONNECTION_ERRORS.search(body):
                    break
            except requests.RequestException:
                pass
            time.sleep(0.2)
        assert process.poll() is None
    finally:
        stop(process)

    assert CONNECTION_ERRORS.search(body)
    assert 'teamspeak_exporter_poll_success 0.0' in body


def test_the_client_reads_the_fake_server_directly():
    with FakeTs3Server(password=PASSWORD) as server:
        client = app.ServerQueryClient.connect(server.host, server.port)
        try:
            client.login('serveradmin', PASSWORD)
            listed = client.serverlist()
            client.use(listed[0]['virtualserver_id'])
            info = client.serverinfo()
        finally:
            client.close()

    assert [s['virtualserver_name'] for s in listed] == names()
    assert all(name in info for name in app.METRICS_NAMES)


def test_the_healthcheck_probe_accepts_a_live_metrics_endpoint(exporter):
    _, port = exporter
    scrape(port, names())

    healthcheck.probe(port)


def test_the_healthcheck_probe_rejects_a_closed_port():
    with pytest.raises(healthcheck.HealthcheckError, match='did not answer'):
        healthcheck.probe(free_port())


@pytest.mark.parametrize('variable', ['http_proxy', 'HTTP_PROXY', 'all_proxy'])
def test_the_healthcheck_probe_ignores_a_proxy_in_the_environment(
    exporter, monkeypatch, variable
):
    # Nothing listens on the proxy port: going through it would fail the probe.
    _, port = exporter
    scrape(port, names())
    monkeypatch.delenv('no_proxy', raising=False)
    monkeypatch.delenv('NO_PROXY', raising=False)
    monkeypatch.setenv(variable, f'http://127.0.0.1:{free_port()}')

    healthcheck.probe(port)
