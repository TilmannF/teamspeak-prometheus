"""Flags as strings until used, the password list, and main() handing
it to every place that censors.
"""

from __future__ import annotations

import pytest

from teamspeak_prometheus import main as main_module
from teamspeak_prometheus.cli import (
    given_values,
    parse_args,
    password_candidates,
)
from teamspeak_prometheus.config import overridden_flags, resolve_config
from teamspeak_prometheus.errors import ExporterError
from teamspeak_prometheus.main import main


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
    args = parse_args([flag, bad])
    env = {variable: good}

    assert getattr(resolve_config(args, env), field) == expected
    assert overridden_flags(args, env) == [f'{flag} ({variable} is set)']


def test_a_malformed_flag_without_override_names_flag_and_variable():
    with pytest.raises(ExporterError) as caught:
        resolve_config(parse_args(['--ts3port', 'nope']), {})

    assert 'TEAMSPEAK_PORT (--ts3port) must be a port number' in str(caught.value)


def test_the_log_level_flag_is_case_insensitive():
    config = resolve_config(parse_args(['--loglevel', 'warning']), {})

    assert config.log_level == 'WARNING'


def test_every_given_password_is_a_secret():
    args = parse_args(['--ts3password', 'from-flag'])

    assert password_candidates(args, {'TEAMSPEAK_PASSWORD': 'from-env'}) == [
        'from-flag',
        'from-env',
    ]
    assert password_candidates(parse_args([]), {}) == []


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

    assert main(argv) == 2

    err = capsys.readouterr().err
    assert 'Invalid configuration' in err
    assert '70000' not in err
    assert '*censored*' in err


def test_main_censors_an_environment_password_equal_to_a_flag_name(clean_env, capsys):
    clean_env.setenv('TEAMSPEAK_PASSWORD', '--ts3port')

    with pytest.raises(SystemExit):
        main(['--ts3port'])

    assert '--ts3port' not in capsys.readouterr().err


def test_main_hands_one_password_list_to_every_censor(clean_env, monkeypatch):
    """The log filter, the service's labels and argparse errors all censor the
    same passwords: the flag one and the environment one, used or not."""

    clean_env.setenv('TEAMSPEAK_PASSWORD', 'from-env')
    seen: dict[str, list[list[str]]] = {'logging': [], 'argparse': [], 'service': []}
    real_configure_logging = main_module.configure_logging
    real_parse_args = main_module.parse_args

    def record_logging(level, secrets=None):
        seen['logging'].append(sorted(secrets or []))
        real_configure_logging(level, secrets)

    def record_parse_args(argv=None, secrets=None):
        seen['argparse'].append(sorted(secrets or []))
        return real_parse_args(argv, secrets)

    def record_serve(config, secrets):
        seen['service'].append(sorted(secrets))
        return 0

    monkeypatch.setattr(main_module, 'configure_logging', record_logging)
    monkeypatch.setattr(main_module, 'parse_args', record_parse_args)
    monkeypatch.setattr(main_module, 'serve', record_serve)
    monkeypatch.setattr(main_module, 'handle_termination', lambda: None)

    assert main(['--ts3password', 'from-flag']) == 0

    both = ['from-env', 'from-flag']
    assert seen['argparse'] == [['from-env']]  # before the flags are parsed
    assert seen['logging'] == [['from-env'], both, both]
    assert seen['service'] == [both]


def test_every_value_of_a_repeated_password_flag_is_remembered():
    args = parse_args(
        ['--ts3password', 'first', '--ts3pass', 'second', '--ts3password=third']
    )

    assert args.ts3password == 'third'  # argparse semantics unchanged: last wins
    assert given_values(args, 'ts3password') == ['first', 'second', 'third']
    assert resolve_config(args, {}).password == 'third'


def test_every_repeated_password_is_a_candidate():
    args = parse_args(['--ts3password', 'first', '--ts3password', 'second'])

    assert password_candidates(args, {'TEAMSPEAK_PASSWORD': 'env'}) == [
        'first',
        'second',
        'env',
    ]


def test_a_single_or_missing_password_flag_still_works():
    assert given_values(parse_args(['--ts3password', 'x']), 'ts3password') == ['x']
    assert given_values(parse_args([]), 'ts3password') == []


def test_main_hands_every_repeated_password_to_every_censor(clean_env, monkeypatch):
    seen: list[list[str]] = []
    real_configure_logging = main_module.configure_logging

    def record_logging(level, secrets=None):
        seen.append(sorted(secrets or []))
        real_configure_logging(level, secrets)

    def record_serve(config, secrets):
        seen.append(sorted(secrets))
        return 0

    monkeypatch.setattr(main_module, 'configure_logging', record_logging)
    monkeypatch.setattr(main_module, 'serve', record_serve)
    monkeypatch.setattr(main_module, 'handle_termination', lambda: None)

    assert main(['--ts3password', 'first', '--ts3password', 'second']) == 0

    assert seen[-1] == seen[-2] == ['first', 'second']  # service, final filter
