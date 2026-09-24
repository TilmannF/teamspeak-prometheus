"""Log redaction: the password and forged lines never reach the log.

Server-provided text is untrusted (AGENTS.md, "Secrets"). These tests feed the
``RedactingFilter`` the way a hostile server would.
"""

from __future__ import annotations

import logging
import sys

import pytest
from prometheus_client import CollectorRegistry

import app
from tests.fakes import FakeTs3Client, factory_for

SECRET = 'fixture-secret'


def formatted(record: logging.LogRecord) -> str:
    return logging.Formatter('%(message)s').format(record)


def record(msg: object, *args: object, exc_info=None) -> logging.LogRecord:
    return logging.LogRecord('test', logging.ERROR, __file__, 1, msg, args, exc_info)


def filtered(msg: object, *args: object, secrets=(SECRET,), exc_info=None) -> str:
    entry = record(msg, *args, exc_info=exc_info)
    assert app.RedactingFilter(list(secrets)).filter(entry) is True
    return formatted(entry)


@pytest.fixture
def redaction():
    """Install the filter on the exporter logger for one test."""

    installed = app.RedactingFilter([SECRET])
    app.log.addFilter(installed)
    yield
    app.log.removeFilter(installed)


# -- redaction ---------------------------------------------------------------


def test_a_secret_in_an_argument_is_censored():
    assert filtered('login failed: %s', f'bad password {SECRET}') == (
        'login failed: bad password *censored*'
    )


def test_a_secret_in_the_message_itself_is_censored():
    assert filtered(f'oops {SECRET}') == 'oops *censored*'


def test_a_secret_inside_an_exception_argument_is_censored():
    err = app.ServerQueryError('use', 1033, f'echo {SECRET}')

    assert SECRET not in filtered('Skipping virtualserver %s: %s', 2, err)


def test_a_secret_in_a_traceback_is_censored():
    try:
        raise RuntimeError(f'boom {SECRET}')
    except RuntimeError:
        output = filtered('Unexpected error during poll', exc_info=sys.exc_info())

    assert 'RuntimeError: boom *censored*' in output
    assert SECRET not in output


def test_every_occurrence_and_every_secret_is_censored():
    output = filtered('%s %s', f'{SECRET}{SECRET}', 'other', secrets=[SECRET, 'other'])

    assert output == '*censored**censored* *censored*'


def test_a_secret_containing_another_is_censored_whole():
    output = filtered('%s', 'abc-long', secrets=['abc', 'abc-long'])

    assert output == '*censored*'


def test_an_empty_password_censors_nothing():
    assert filtered('Host: %s', 'localhost', secrets=['']) == 'Host: localhost'


def test_mapping_arguments_are_cleaned():
    entry = logging.LogRecord(
        'test', logging.INFO, __file__, 1, 'name=%(name)s', ({'name': SECRET},), None
    )
    app.RedactingFilter([SECRET]).filter(entry)

    assert formatted(entry) == 'name=*censored*'


# -- forged lines ------------------------------------------------------------


def test_line_breaks_in_arguments_cannot_forge_log_lines():
    output = filtered(
        'Skipping virtualserver %s: %s',
        2,
        'not running\n2026-01-01 00:00:00,000 CRITICAL forged\r\nmore',
    )

    assert '\n' not in output and '\r' not in output
    assert output.endswith(
        'not running\\n2026-01-01 00:00:00,000 CRITICAL forged\\r\\nmore'
    )


@pytest.mark.parametrize(
    ('raw', 'escaped'),
    [
        ('\x1b[31m', '\\x1b[31m'),  # terminal escape sequence
        ('\x00', '\\x00'),
        ('\x7f', '\\x7f'),
        ('\x85', '\\x85'),  # C1 "next line"
        (' ', '\\u2028'),  # Unicode line separator
        (' ', '\\u2029'),  # Unicode paragraph separator
    ],
)
def test_control_characters_in_arguments_are_escaped(raw: str, escaped: str):
    assert filtered('name: %s', f'a{raw}b') == f'name: a{escaped}b'


def test_tabs_and_unicode_text_stay_readable():
    assert filtered('name: %s', 'Zweiter\tServer Ümläute') == (
        'name: Zweiter\tServer Ümläute'
    )


def test_the_exporters_own_multi_line_message_is_kept():
    banner = 'TS3 SETTINGS:\nHost: localhost\nPassword: *censored*'

    assert filtered(banner) == banner


def test_a_number_equal_to_the_password_is_censored():
    # TEAMSPEAK_PASSWORD=8000 with the default metrics port 8000
    assert filtered('Started metrics endpoint on port %s', 8000, secrets=['8000']) == (
        'Started metrics endpoint on port *censored*'
    )
    assert filtered('port %d', 8000, secrets=['8000']) == 'port *censored*'


def test_a_secret_split_across_template_and_argument_is_censored():
    assert filtered('value: sec%s', 'ret', secrets=['secret']) == 'value: *censored*'


def test_a_broken_format_string_neither_raises_nor_leaks(capsys):
    entry = record('port %d', f'{SECRET}\nforged')

    app.RedactingFilter([SECRET]).filter(entry)
    output = formatted(entry)

    assert SECRET not in output
    assert '\n' not in output
    assert 'port %d' in output
    assert SECRET not in capsys.readouterr().err


def test_numeric_arguments_keep_their_format():
    assert filtered('waiting %.0fs, %d left', 2.4, 3) == 'waiting 2s, 3 left'


# -- wiring ------------------------------------------------------------------


def test_configure_logging_installs_exactly_one_filter():
    before = list(app.log.filters)
    try:
        app.configure_logging('INFO', secrets=['first'])
        app.configure_logging('INFO', secrets=['second'])

        installed = [f for f in app.log.filters if isinstance(f, app.RedactingFilter)]
        assert len(installed) == 1
        assert installed[0].secrets == ['second']
    finally:
        app.log.filters[:] = before


def test_a_hostile_server_cannot_get_the_password_into_the_log(caplog, redaction):
    # The server echoes the submitted password and embeds a forged log line in
    # its error text, for a rejected login and for a failing virtualserver.
    config = app.Config(
        host='ts.example.com',
        port=10011,
        username='serveradmin',
        password=SECRET,
        metrics_port=8000,
    )
    client = FakeTs3Client(
        offline={2},
        offline_message=f'not running {SECRET}\n2026-01-01 CRITICAL forged',
    )
    registry = CollectorRegistry()
    service = app.Teamspeak3MetricService(
        config,
        app.build_gauges(registry),
        app.build_exporter_metrics(registry),
        factory_for(client),
    )
    service.poll()
    client.login_error = app.LoginFailed('login', 520, f'invalid password {SECRET}')
    service.poll()

    assert caplog.records
    assert SECRET not in caplog.text
    assert not any(line.startswith('2026-01-01') for line in caplog.text.splitlines())
    assert '*censored*' in caplog.text
