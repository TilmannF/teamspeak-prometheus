"""No command-line tool prints its password, however it is chosen.

The exporter, the healthcheck, the harness and the fake server, run with
passwords that collide with their output. A new tool gets a row in
tool_cases() -- see AGENTS.md, "Secrets".
"""

from __future__ import annotations

import socket
import subprocess
import sys

import pytest

from tests.smoke_support import PASSWORD, app_env, free_port, run_briefly, run_tool

pytestmark = pytest.mark.smoke


def test_the_harness_never_prints_a_password_equal_to_its_metrics_port():
    port = str(free_port())

    result = run_tool(
        'tests.exporter_harness',
        '--metricsport',
        port,
        '--ts3password',
        port,
        '--iterations',
        '1',
        '--interval',
        '0.2',
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'Scrape it with' in output
    assert port not in output


def test_the_harness_never_prints_its_password_when_the_port_is_taken():
    # the harness serves on loopback only, so taking the loopback port is
    # enough -- and binds no socket to all interfaces
    with socket.socket() as taken:
        taken.bind(('127.0.0.1', 0))
        taken.listen()
        port = str(taken.getsockname()[1])

        result = run_tool(
            'tests.exporter_harness',
            '--metricsport',
            port,
            '--ts3password',
            port,
            '--iterations',
            '1',
        )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert 'Could not listen on port' in output
    assert port not in output


def test_the_fake_server_never_prints_a_password_equal_to_its_port():
    port = str(free_port())
    process = subprocess.Popen(
        [
            sys.executable,
            '-u',
            '-m',
            'tests.fake_ts3_server',
            '--port',
            port,
            '--password',
            port,
        ],
        env=app_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        first_line = process.stdout.readline()
    finally:
        process.terminate()
        process.communicate(timeout=10)

    assert 'Fake TS3 ServerQuery listening on' in first_line
    assert port not in first_line


def tool_cases() -> list[tuple[str, list[str], dict[str, str], str]]:
    port = str(free_port())
    unreachable = str(free_port())
    dash = '-' + PASSWORD
    return [
        (
            'exporter, password = metrics port',
            ['app.py'],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'TEAMSPEAK_PASSWORD': port,
                'METRICS_PORT': port,
            },
            port,
        ),
        (
            'exporter, password = TeamSpeak port',
            ['app.py', '--ts3password', unreachable],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'METRICS_PORT': port,
            },
            unreachable,
        ),
        (
            'exporter, dash password',
            ['app.py', '--ts3password=' + dash],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'METRICS_PORT': port,
            },
            dash,
        ),
        (
            'exporter, mistyped flag with dash password',
            ['app.py', '--ts3pasword', dash],
            {},
            dash,
        ),
        (
            'exporter, password = the censoring marker',
            ['app.py'],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'TEAMSPEAK_PASSWORD': '*censored*',
                'METRICS_PORT': port,
            },
            '*censored*',
        ),
        (
            'exporter, password inside the marker',
            ['app.py'],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'TEAMSPEAK_PASSWORD': 'censor',
                'METRICS_PORT': port,
            },
            'censor',
        ),
        (
            'exporter, environment password = a flag name',
            ['app.py', '--ts3port'],
            {'TEAMSPEAK_PASSWORD': '--ts3port'},
            '--ts3port',
        ),
        (
            'exporter, flag password = a flag name',
            ['app.py', '--ts3password=--metricsport', '--metricsport'],
            {},
            '--metricsport',
        ),
        (
            'exporter, repeated flag, first password = metrics port',
            ['app.py', '--ts3password', port, '--ts3password', 'final-' + PASSWORD],
            {
                'TEAMSPEAK_HOST': '127.0.0.1',
                'TEAMSPEAK_PORT': unreachable,
                'METRICS_PORT': port,
            },
            port,
        ),
        (
            'exporter --help, environment password = a default',
            ['app.py', '--help'],
            {'TEAMSPEAK_PASSWORD': '8000'},
            '8000',
        ),
        (
            'exporter --help, flag password in the help text',
            ['app.py', '--ts3password', '86400', '--help'],
            {},
            '86400',
        ),
        (
            'healthcheck, password = metrics port',
            ['healthcheck.py'],
            {'TEAMSPEAK_PASSWORD': port, 'METRICS_PORT': port},
            port,
        ),
        (
            'harness, password = metrics port',
            [
                '-m',
                'tests.exporter_harness',
                '--metricsport',
                port,
                '--ts3password',
                port,
                '--iterations',
                '1',
                '--interval',
                '0.2',
            ],
            {},
            port,
        ),
        (
            'harness, dash password',
            [
                '-m',
                'tests.exporter_harness',
                '--metricsport',
                port,
                '--ts3password=' + dash,
                '--iterations',
                '1',
                '--interval',
                '0.2',
            ],
            {},
            dash,
        ),
        (
            'fake server, password = its port',
            ['-m', 'tests.fake_ts3_server', '--port', port, '--password', port],
            {},
            port,
        ),
        (
            'harness, password = the censoring marker',
            [
                '-m',
                'tests.exporter_harness',
                '--metricsport',
                port,
                '--ts3password',
                '*censored*',
                '--iterations',
                '1',
                '--interval',
                '0.2',
            ],
            {},
            '*censored*',
        ),
        (
            'fake server, password = a flag name',
            ['-m', 'tests.fake_ts3_server', '--password=--port', '--port'],
            {},
            '--port',
        ),
        (
            'harness, repeated flag, first password = metrics port',
            [
                '-m',
                'tests.exporter_harness',
                '--metricsport',
                port,
                '--ts3password',
                port,
                '--ts3password',
                'final-' + PASSWORD,
                '--iterations',
                '1',
                '--interval',
                '0.2',
            ],
            {},
            port,
        ),
        (
            'fake server, repeated flag, first password = its port',
            [
                '-m',
                'tests.fake_ts3_server',
                '--port',
                port,
                '--password',
                port,
                '--password',
                'final-' + PASSWORD,
            ],
            {},
            port,
        ),
        (
            'harness --help, password in the help text',
            ['-m', 'tests.exporter_harness', '--ts3password', 'fake', '--help'],
            {},
            'fake',
        ),
        (
            'fake server --help, password in the help text',
            ['-m', 'tests.fake_ts3_server', '--password', 'docs/testing.md', '--help'],
            {},
            'docs/testing.md',
        ),
        (
            'fake server, mistyped flag with dash password',
            ['-m', 'tests.fake_ts3_server', '--pasword', dash],
            {},
            dash,
        ),
    ]


@pytest.mark.parametrize(
    'case', range(len(tool_cases())), ids=[case[0] for case in tool_cases()]
)
def test_no_tool_prints_its_password(case):
    name, argv, env, secret = tool_cases()[case]

    output = run_briefly(argv, env)

    assert output.strip(), f'{name}: no output at all, the test proves nothing'
    assert secret not in output, f'{name} printed its password'
