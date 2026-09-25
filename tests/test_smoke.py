"""End-to-end: boot the exporter against the fake TS3 server and scrape it.

No TeamSpeak server is needed. Marked ``smoke`` because it spawns a subprocess
and opens sockets.
"""

from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest
import requests

import app
import healthcheck
from tests.fake_ts3_server import FORGED_LOG_LINE, FakeTs3Server, virtualservers

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


@pytest.mark.parametrize(
    ('submitted', 'reason', 'redacted'),
    [
        (PASSWORD, 'query', 'server is not running *censored*\\n'),
        ('wrong-' + PASSWORD, 'login', 'invalid password *censored*\\n'),
    ],
    ids=['echo-on-failing-virtualserver', 'echo-on-rejected-login'],
)
def test_a_hostile_server_cannot_put_the_password_or_forged_lines_in_the_log(
    submitted, reason, redacted
):
    with FakeTs3Server(password=PASSWORD, hostile=True) as server:
        output = run_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': submitted,
            },
            re.compile(rf'poll_errors_total{{reason="{reason}"}} [1-9]'),
        )

    assert submitted not in output
    # the server's text is logged -- censored, and on the same line, escaped
    assert redacted + FORGED_LOG_LINE in output
    assert not any(line.startswith(FORGED_LOG_LINE) for line in output.splitlines())


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


@pytest.fixture
def trickling_server():
    """A TCP server that greets like TeamSpeak, then sends one byte every
    0.2s forever, never ending the line."""

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    stop_event = threading.Event()

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:  # closed before anyone connected
            return
        with connection:
            connection.sendall(b'TS3\n\rWelcome\n\r')
            while not stop_event.is_set():
                try:
                    connection.sendall(b'x')
                except OSError:
                    return
                time.sleep(0.2)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    stop_event.set()
    listener.close()


def test_a_trickling_server_cannot_stall_a_session(trickling_server):
    client = app.ServerQueryClient.connect(
        '127.0.0.1', trickling_server, session_timeout=2
    )
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        client.serverlist()

    assert time.monotonic() - started < 4
    client.close()


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


def test_a_hostile_server_cannot_put_the_password_into_the_metrics():
    # virtualserver 1 is named "Test Server <password>" by the hostile server
    with FakeTs3Server(password=PASSWORD, hostile=True) as server:
        output, body = scrape_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': PASSWORD,
            },
            re.compile(r'virtualserver_name="Test Server \*censored\*"'),
        )

    assert PASSWORD not in body
    assert PASSWORD not in output


# -- the test tools never print their password, even where it collides -------


def run_tool(*argv: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, '-u', '-m', *argv],
        env=app_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


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


# -- no tool prints its password, however it is chosen -------------------------
#
# One row per tool and awkward password: equal to the port the tool prints,
# looking like an option, equal to a flag name, or equal to (or inside) the
# censoring marker itself. A new command-line tool gets a row here.


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


def test_an_overridden_flag_password_is_censored_in_the_metrics():
    # --ts3password old-secret, overridden by TEAMSPEAK_PASSWORD: a rotation
    # leftover. The server names a virtualserver after the old one.
    old = 'old-' + PASSWORD
    with FakeTs3Server(password=PASSWORD, names=[f'Server {old}']) as server:
        output, body = scrape_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': PASSWORD,
            },
            re.compile(r'virtualserver_name="Server \*censored\*"'),
            argv=('--ts3password', old),
        )

    assert old not in body
    assert old not in output


def non_loopback_address() -> str | None:
    """An address of this machine other than 127.0.0.1, if it has one."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('192.0.2.1', 9))  # TEST-NET-1; nothing is sent
        except OSError:
            return None
        address = probe.getsockname()[0]
    return None if address.startswith('127.') else address


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
