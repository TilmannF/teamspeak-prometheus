"""End-to-end: the password never reaches the log or /metrics.

Against the hostile fake server, which echoes the password, forges log
lines and names virtualservers after it.
"""

from __future__ import annotations

import re

import pytest

from teamspeak_prometheus.redaction import RedactingFilter
from tests.fake_ts3_server import FORGED_LOG_LINE, FakeTs3Server
from tests.smoke_support import PASSWORD, names, run_app, scrape, scrape_app, stop

pytestmark = pytest.mark.smoke


def test_the_password_is_never_printed(exporter):
    process, port = exporter
    scrape(port, names())

    output = stop(process)

    assert PASSWORD not in output
    assert '*censored*' in output


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


@pytest.mark.parametrize(
    'submitted',
    [f'wrong\n{PASSWORD}', f'wrong\t{PASSWORD}', f'wrong\x1b[31m{PASSWORD}'],
    ids=['newline', 'tab', 'escape-sequence'],
)
def test_a_password_with_a_control_character_never_reaches_the_log(submitted):
    # The hostile server rejects the login and echoes the submitted password;
    # the exporter must censor it before escaping the control character.
    with FakeTs3Server(password=PASSWORD, hostile=True) as server:
        output = run_app(
            {
                'TEAMSPEAK_HOST': server.host,
                'TEAMSPEAK_PORT': str(server.port),
                'TEAMSPEAK_PASSWORD': submitted,
            },
            re.compile(r'poll_errors_total\{reason="login"\} [1-9]'),
        )

    escaped = str(RedactingFilter.escape(submitted))
    assert submitted not in output
    assert escaped not in output
    assert 'invalid password *censored*' in output
