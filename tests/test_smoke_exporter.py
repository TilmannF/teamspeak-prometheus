"""End-to-end: the exporter against the fake server, as a process.

Serving metrics, flood protection, outages, stopped and huge virtualservers,
large throttled hosts, a stalled session, and shutdown on SIGTERM.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time

import pytest
import requests

from teamspeak_prometheus.metrics import METRICS_NAMES
from teamspeak_prometheus.serverquery import ServerQueryClient
from tests.fake_ts3_server import FakeTs3Server
from tests.smoke_support import (
    CONNECTION_ERRORS,
    HTTP,
    PASSWORD,
    app_env,
    free_port,
    names,
    run_app,
    scrape,
    scrape_app,
    start_exporter,
    stop,
    terminate_and_time,
    wait_for_metrics,
)

pytestmark = pytest.mark.smoke


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
    assert exposed == {'teamspeak_' + name for name in METRICS_NAMES}


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
        client = ServerQueryClient.connect(server.host, server.port)
        try:
            client.login('serveradmin', PASSWORD)
            listed = client.serverlist()
            client.use(listed[0]['virtualserver_id'])
            info = client.serverinfo()
        finally:
            client.close()

    assert [s['virtualserver_name'] for s in listed] == names()
    assert all(name in info for name in METRICS_NAMES)


def test_a_stopped_virtualserver_does_not_fail_the_poll():
    with FakeTs3Server(password=PASSWORD, stopped=frozenset({2})) as server:
        output = run_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': PASSWORD,
            },
            re.compile(r'^teamspeak_exporter_poll_success 1\.0', re.M),
        )

    assert "Virtualserver 2 'Zweiter Server' is offline" in output
    assert 'Skipping' not in output


def test_a_trickling_server_cannot_stall_a_session(trickling_server):
    client = ServerQueryClient.connect('127.0.0.1', trickling_server, session_timeout=2)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        client.serverlist()

    assert time.monotonic() - started < 4
    client.close()


@pytest.mark.parametrize('target', ['idle', 'stuck-in-a-read'])
def test_sigterm_stops_the_exporter_cleanly(target, trickling_server):
    # idle: between polls, TeamSpeak unreachable. stuck-in-a-read: mid-session,
    # blocked on a server that never finishes its line.
    port = free_port()
    ts3_port = str(free_port() if target == 'idle' else trickling_server)
    process = subprocess.Popen(
        [sys.executable, '-u', 'app.py'],
        env=app_env(
            METRICS_PORT=str(port),
            TEAMSPEAK_HOST='127.0.0.1',
            TEAMSPEAK_PORT=ts3_port,
            TEAMSPEAK_POLL_INTERVAL='60',
        ),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_for_metrics(port)
        time.sleep(0.5)  # let the first poll start
        elapsed, output = terminate_and_time(process)
    finally:
        if process.poll() is None:
            process.kill()

    assert process.returncode == 0
    assert elapsed < 3
    assert 'Stopped' in output
    assert 'Traceback' not in output


def test_a_throttled_host_is_read_completely_past_the_base_budget():
    # 20 virtualservers need 43 commands; at 10 per second the fake throttles
    # for about 4s -- more than the deliberately short 2s base budget.
    with FakeTs3Server(
        password=PASSWORD, virtualserver_count=20, flood_limit=10, flood_window=1
    ) as server:
        client = ServerQueryClient.connect(server.host, server.port, session_timeout=2)
        try:
            client.login('serveradmin', PASSWORD)
            listed = client.serverlist()
            for entry in listed:
                client.use(entry['virtualserver_id'])
                client.serverinfo()
        finally:
            client.close()

    assert len(listed) == 20


def test_a_huge_virtualserver_name_is_cut_in_the_metrics():
    with FakeTs3Server(password=PASSWORD, names=['n' * 5_000]) as server:
        _, body = scrape_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': PASSWORD,
            },
            re.compile(r'^teamspeak_exporter_poll_success 1\.0', re.M),
        )

    labels = set(re.findall(r'virtualserver_name="([^"]*)"', body))
    assert 'n' * 255 + '…' in labels
    assert max(len(label) for label in labels) == 256
