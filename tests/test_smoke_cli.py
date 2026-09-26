"""End-to-end: the command line and configuration of the real processes.

Bad command lines, flags overridden by the environment, empty variables,
configured values that try to forge output lines, the loopback-only harness.
"""

from __future__ import annotations

import re
import subprocess
import sys

import pytest
import requests

from tests.fake_ts3_server import FakeTs3Server
from tests.smoke_support import (
    FORGED,
    HTTP,
    PASSWORD,
    app_env,
    free_port,
    names,
    non_loopback_address,
    run_app,
    run_briefly,
    scrape,
)

pytestmark = pytest.mark.smoke


@pytest.mark.parametrize(
    ('argv', 'env', 'secret'),
    [
        (['--ts3pasword', PASSWORD], {}, PASSWORD),
        (['--ts3=' + PASSWORD], {}, PASSWORD),
        (['--ts3password', '70000', '--ts3port', '70000'], {}, '70000'),
        ([], {'TEAMSPEAK_PASSWORD': '70000', 'METRICS_PORT': '70000'}, '70000'),
    ],
    ids=['typo', 'ambiguous', 'invalid-equals-password', 'environment'],
)
def test_a_bad_command_line_exits_without_printing_the_password(argv, env, secret):
    result = subprocess.run(
        [sys.executable, 'app.py', *argv],
        env=app_env(**env),
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert 'error' in (result.stdout + result.stderr).lower()
    assert secret not in result.stdout + result.stderr


def test_a_malformed_flag_overridden_by_the_environment_does_not_stop_startup():
    with FakeTs3Server(password=PASSWORD) as server:
        output = run_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': PASSWORD,
                'LOG_LEVEL': 'info',
            },
            re.compile(r'^teamspeak_exporter_poll_success 1\.0', re.M),
            argv=('--ts3port', 'nope', '--loglevel', 'chatty'),
        )

    assert 'Ignoring --ts3port (TEAMSPEAK_PORT is set)' in output
    assert 'Ignoring --loglevel (LOG_LEVEL is set)' in output
    assert PASSWORD not in output


@pytest.mark.parametrize(
    ('argv', 'env'),
    [
        (['app.py'], {'TEAMSPEAK_HOST': f'ts.example.com\n{FORGED}'}),
        (['app.py'], {'TEAMSPEAK_USERNAME': f'serveradmin\r\n{FORGED}'}),
        (['app.py', '--ts3host', f'ts.example.com\u2028{FORGED}'], {}),
        (
            [
                '-m',
                'tests.exporter_harness',
                '--ts3host',
                f'x\n{FORGED}',
                '--ts3port',
                '1',
                '--iterations',
                '1',
                '--interval',
                '0.2',
            ],
            {},
        ),
    ],
    ids=['exporter-host', 'exporter-username', 'exporter-host-flag', 'harness-host'],
)
def test_a_configured_value_cannot_forge_output_lines(argv, env):
    output = run_briefly(
        argv,
        {'TEAMSPEAK_PORT': str(free_port()), 'METRICS_PORT': str(free_port()), **env}
        if argv[0] == 'app.py'
        else env,
    )

    assert 'TS3 SETTINGS' in output
    assert FORGED in output  # logged, escaped onto the line it belongs to
    assert not any(line.startswith(FORGED) for line in output.splitlines())


@pytest.mark.parametrize('variable', ['TEAMSPEAK_HOST', 'TEAMSPEAK_USERNAME'])
def test_an_empty_variable_stops_the_exporter_with_a_clear_error(variable):
    result = subprocess.run(
        [sys.executable, 'app.py'],
        env=app_env(**{variable: ''}),
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert f'{variable} (--' in result.stderr
    assert 'must not be empty' in result.stderr


def test_the_harness_serves_metrics_on_loopback_only(exporter):
    # The harness is a local test tool: its metrics must not be reachable from
    # the network (CodeQL py/bind-socket-all-network-interfaces).
    _, port = exporter
    scrape(port, names())
    address = non_loopback_address()
    if address is None:
        pytest.skip('this machine has no non-loopback IPv4 address')

    with pytest.raises(requests.ConnectionError):
        HTTP.get(f'http://{address}:{port}/metrics', timeout=2)
