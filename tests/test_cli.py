"""The command line and startup: flags are strings until used, errors never
repeat a value, and redaction is active before the first log line.
"""

from __future__ import annotations

import ast
import logging
from pathlib import Path

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
        # a password can itself look like an option
        ['--ts3pasword', '-' + SECRET],
        ['--ts3pasword', '--' + SECRET],
        ['--ts3pasword', '--' + SECRET + '=x'],
        ['-' + SECRET],
    ],
    ids=[
        'typo',
        'typo-equals',
        'positional',
        'ambiguous',
        'other-unknown',
        'dash-value',
        'double-dash-value',
        'double-dash-value-with-equals',
        'lone-dash-value',
    ],
)
def test_argument_errors_never_print_a_value(argv, capsys):
    err = parse_error(argv, capsys)

    assert 'error:' in err
    assert SECRET not in err


def test_unknown_arguments_are_hidden_entirely(capsys):
    # A mistyped flag name and a dash-prefixed password are indistinguishable,
    # so no unknown token is shown -- only that values were hidden.
    err = parse_error(['--ts3pasword', '-' + SECRET], capsys)

    assert 'unrecognized arguments: … … (argument values hidden; see --help)' in err


def test_known_flags_stay_visible(capsys):
    err = parse_error(['--ts3password=' + SECRET, '--ts3host'], capsys)

    assert 'argument --ts3host: expected one argument' in err
    assert SECRET not in err


def test_masking_replaces_whole_values_only(capsys):
    # the password "3" must not turn "--ts3port" into "--ts…port"
    err = parse_error(['--ts3password', '3', '--ts3=3'], capsys)

    assert 'could match --ts3host, --ts3port' in err
    assert 'ambiguous option: … could match' in err


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


# -- every command line in the repository masks values ------------------------

REPOSITORY = Path(__file__).parent.parent


def python_files() -> list[Path]:
    return [
        path
        for path in REPOSITORY.rglob('*.py')
        if not any(
            part.startswith('.') or part == '__pycache__'
            for part in path.relative_to(REPOSITORY).parts
        )
    ]


def plain_parsers(path: Path) -> list[int]:
    """Lines constructing an ``ArgumentParser`` directly."""

    return [
        node.lineno
        for node in ast.walk(ast.parse(path.read_text(), str(path)))
        if isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == 'ArgumentParser'
            )
            or (isinstance(node.func, ast.Name) and node.func.id == 'ArgumentParser')
        )
    ]


def test_the_scan_sees_the_whole_repository():
    names = {path.relative_to(REPOSITORY).as_posix() for path in python_files()}

    assert {'app.py', 'healthcheck.py', 'tests/exporter_harness.py'} <= names
    assert not any(name.startswith('.venv/') for name in names)


def test_no_command_line_parser_bypasses_value_masking():
    offenders = {
        path.relative_to(REPOSITORY).as_posix(): lines
        for path in python_files()
        if (lines := plain_parsers(path))
    }

    assert offenders == {}, 'use app.SafeArgumentParser, see AGENTS.md "Secrets"'


@pytest.mark.parametrize(
    'argv',
    [
        ['--ts3pasword', SECRET],
        ['--ts3pasword=' + SECRET],
        [SECRET],
    ],
    ids=['typo', 'typo-equals', 'positional'],
)
@pytest.mark.parametrize('tool', ['harness', 'fake-server'])
def test_the_test_tools_never_print_a_value_either(tool, argv, capsys):
    from tests import exporter_harness, fake_ts3_server

    main = {'harness': exporter_harness.main, 'fake-server': fake_ts3_server.main}[tool]

    with pytest.raises(SystemExit) as caught:
        main(argv)

    err = capsys.readouterr().err
    assert caught.value.code == 2
    assert 'unrecognized arguments' in err
    assert SECRET not in err
