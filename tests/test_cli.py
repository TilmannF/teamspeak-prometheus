"""The command line and startup: flags are strings until used, errors never
repeat a value, and redaction is active before the first log line.
"""

from __future__ import annotations

import logging

import pytest

import app

SECRET = 'fixture-secret'
ENVIRONMENT = [
    'TEAMSPEAK_HOST',
    'TEAMSPEAK_PORT',
    'TEAMSPEAK_USERNAME',
    'TEAMSPEAK_PASSWORD',
    'METRICS_PORT',
    'TEAMSPEAK_POLL_INTERVAL',
    'LOG_LEVEL',
]


@pytest.fixture
def clean_env(monkeypatch):
    """No exporter variables, and logging restored after ``main()`` reset it."""

    for variable in ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)
    root = logging.getLogger()
    handlers, level, filters = root.handlers[:], root.level, app.log.filters[:]
    yield monkeypatch
    root.handlers[:] = handlers
    root.setLevel(level)
    app.log.filters[:] = filters


def parse_error(argv: list[str], capsys) -> str:
    with pytest.raises(SystemExit) as caught:
        app.parse_args(argv)
    assert caught.value.code == 2
    return capsys.readouterr().err


# -- argparse errors never repeat a value ------------------------------------


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3pasword', SECRET],  # typo: unrecognized flag and its value
        ['--ts3pasword=' + SECRET],
        [SECRET],  # stray positional
        ['--ts3=' + SECRET],  # ambiguous abbreviation
        ['--ts3password', SECRET, '--bogus', 'x'],
    ],
    ids=['typo', 'typo-equals', 'positional', 'ambiguous', 'other-unknown'],
)
def test_argument_errors_never_print_a_value(argv, capsys):
    err = parse_error(argv, capsys)

    assert 'error:' in err
    assert SECRET not in err


def test_argument_errors_still_name_the_offending_flag(capsys):
    err = parse_error(['--ts3pasword', SECRET], capsys)

    assert 'unrecognized arguments: --ts3pasword …' in err


def test_masking_replaces_whole_values_only(capsys):
    # the password "3" must not turn "--ts3port" into "--ts…port"
    err = parse_error(['--ts3password', '3', '--bogus', '3'], capsys)

    assert 'unrecognized arguments: --bogus …' in err


def test_a_flag_missing_its_value_is_still_reported(capsys):
    err = parse_error(['--ts3port'], capsys)

    assert 'argument --ts3port: expected one argument' in err


# -- flags are only validated when used --------------------------------------


@pytest.mark.parametrize(
    ('flag', 'bad', 'variable', 'good', 'field', 'expected'),
    [
        ('--ts3port', 'nope', 'TEAMSPEAK_PORT', '10011', 'port', 10011),
        ('--metricsport', 'nope', 'METRICS_PORT', '9000', 'metrics_port', 9000),
        (
            '--pollinterval',
            'soon',
            'TEAMSPEAK_POLL_INTERVAL',
            '15',
            'poll_interval',
            15,
        ),
        ('--loglevel', 'chatty', 'LOG_LEVEL', 'debug', 'log_level', 'DEBUG'),
    ],
)
def test_a_malformed_flag_overridden_by_the_environment_is_ignored(
    flag, bad, variable, good, field, expected
):
    args = app.parse_args([flag, bad])
    env = {variable: good}

    assert getattr(app.resolve_config(args, env), field) == expected
    assert app.overridden_flags(args, env) == [f'{flag} ({variable} is set)']


def test_a_malformed_flag_without_override_names_flag_and_variable():
    with pytest.raises(app.ExporterError) as caught:
        app.resolve_config(app.parse_args(['--ts3port', 'nope']), {})

    assert 'TEAMSPEAK_PORT (--ts3port) must be a port number' in str(caught.value)


def test_the_log_level_flag_is_case_insensitive():
    config = app.resolve_config(app.parse_args(['--loglevel', 'warning']), {})

    assert config.log_level == 'WARNING'


# -- main: redaction is active before configuration errors -------------------


def test_every_given_password_is_a_secret():
    args = app.parse_args(['--ts3password', 'from-flag'])

    assert app.password_candidates(args, {'TEAMSPEAK_PASSWORD': 'from-env'}) == [
        'from-flag',
        'from-env',
    ]
    assert app.password_candidates(app.parse_args([]), {}) == []


@pytest.mark.parametrize(
    ('argv', 'env'),
    [
        # Codex's case: an invalid value that equals the password
        (['--ts3password', '70000', '--ts3port', '70000'], {}),
        ([], {'TEAMSPEAK_PASSWORD': '70000', 'TEAMSPEAK_PORT': '70000'}),
        # an overridden flag password is still a secret
        (
            ['--ts3password', '70000'],
            {'TEAMSPEAK_PASSWORD': 'x', 'METRICS_PORT': '70000'},
        ),
    ],
    ids=['flag', 'environment', 'overridden-flag'],
)
def test_a_configuration_error_never_prints_the_password(argv, env, clean_env, capsys):
    for variable, value in env.items():
        clean_env.setenv(variable, value)

    assert app.main(argv) == 2

    err = capsys.readouterr().err
    assert 'Invalid configuration' in err
    assert '70000' not in err
    assert '*censored*' in err
