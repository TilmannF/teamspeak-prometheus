"""Configuration resolution: defaults, environment precedence, validation."""

from __future__ import annotations

import pytest

from teamspeak_prometheus.cli import parse_args
from teamspeak_prometheus.config import (
    Config,
    log_settings,
    overridden_flags,
    resolve_config,
)
from teamspeak_prometheus.errors import ExporterError


def resolve(argv: list[str] | None = None, **env: str) -> Config:
    return resolve_config(parse_args(argv or []), env)


def test_defaults_match_the_documented_values():
    config = resolve()

    assert config == Config(
        host='localhost',
        port=10011,
        username='serveradmin',
        password='',
        metrics_port=8000,
        poll_interval=5.0,
        log_level='INFO',
    )


def test_command_line_arguments_are_used_when_no_environment_is_set():
    config = resolve(
        [
            '--ts3host',
            'example.com',
            '--ts3port',
            '12345',
            '--ts3username',
            'ExampleUser',
            '--ts3password',
            'SomePassword',
            '--metricsport',
            '8080',
            '--pollinterval',
            '30',
            '--loglevel',
            'debug',
        ]
    )

    assert config.host == 'example.com'
    assert config.port == 12345
    assert config.username == 'ExampleUser'
    assert config.password == 'SomePassword'
    assert config.metrics_port == 8080
    assert config.poll_interval == 30
    assert config.log_level == 'DEBUG'


def test_environment_overrides_command_line_arguments():
    config = resolve(
        ['--ts3host', 'from-flag', '--ts3username', 'from-flag', '--pollinterval', '9'],
        TEAMSPEAK_HOST='from-env',
        TEAMSPEAK_USERNAME='env-user',
        TEAMSPEAK_PASSWORD='env-password',
        TEAMSPEAK_POLL_INTERVAL='15',
        LOG_LEVEL='warning',
    )

    assert config.host == 'from-env'
    assert config.username == 'env-user'
    assert config.password == 'env-password'
    assert config.poll_interval == 15
    assert config.log_level == 'WARNING'


def test_ports_from_the_environment_are_integers():
    config = resolve(TEAMSPEAK_PORT='10022', METRICS_PORT='9000')

    assert config.port == 10022
    assert config.metrics_port == 9000


def test_a_poll_interval_of_one_second_is_allowed():
    assert resolve(TEAMSPEAK_POLL_INTERVAL='1').poll_interval == 1


def test_a_too_short_poll_interval_names_the_limits():
    with pytest.raises(ExporterError, match='at least 1 and at most 86400'):
        resolve(TEAMSPEAK_POLL_INTERVAL='0.5')


def test_a_poll_interval_of_one_day_is_allowed():
    assert resolve(TEAMSPEAK_POLL_INTERVAL='86400').poll_interval == 86400


def test_a_fractional_poll_interval_is_allowed():
    assert resolve(TEAMSPEAK_POLL_INTERVAL='2.5').poll_interval == 2.5


@pytest.mark.parametrize(
    ('variable', 'value'),
    [
        ('TEAMSPEAK_PORT', 'not-a-port'),
        ('METRICS_PORT', 'not-a-port'),
        ('METRICS_PORT', '70000'),
        ('TEAMSPEAK_POLL_INTERVAL', 'soon'),
        ('TEAMSPEAK_POLL_INTERVAL', '0'),
        ('TEAMSPEAK_POLL_INTERVAL', '-5'),
        ('TEAMSPEAK_POLL_INTERVAL', '0.000001'),
        ('TEAMSPEAK_POLL_INTERVAL', '0.5'),
        ('TEAMSPEAK_POLL_INTERVAL', '0.999'),
        ('TEAMSPEAK_POLL_INTERVAL', 'nan'),
        ('TEAMSPEAK_POLL_INTERVAL', 'inf'),
        ('TEAMSPEAK_POLL_INTERVAL', '1e10'),
        ('TEAMSPEAK_POLL_INTERVAL', '86401'),
        ('LOG_LEVEL', 'chatty'),
    ],
)
def test_invalid_values_fail_with_a_clear_error(variable: str, value: str):
    with pytest.raises(ExporterError, match=variable):
        resolve(**{variable: value})


def test_an_overridden_flag_is_reported():
    args = parse_args(['--ts3host', 'from-flag', '--ts3port', '10011'])

    overridden = overridden_flags(
        args, {'TEAMSPEAK_HOST': 'from-env', 'TEAMSPEAK_PORT': '10011'}
    )

    assert overridden == ['--ts3host (TEAMSPEAK_HOST is set)']


def test_an_overridden_password_flag_is_reported_without_either_value():
    args = parse_args(['--ts3password', 'flag-secret'])

    overridden = overridden_flags(args, {'TEAMSPEAK_PASSWORD': 'env-secret'})

    assert overridden == ['--ts3password (TEAMSPEAK_PASSWORD is set)']


def test_an_invalid_flag_overridden_by_a_valid_env_value_is_only_reported():
    args = parse_args(['--ts3port', '70000', '--pollinterval', 'inf'])
    env = {'TEAMSPEAK_PORT': '10011', 'TEAMSPEAK_POLL_INTERVAL': '5'}

    config = resolve_config(args, env)

    assert config.port == 10011
    assert overridden_flags(args, env) == [
        '--ts3port (TEAMSPEAK_PORT is set)',
        '--pollinterval (TEAMSPEAK_POLL_INTERVAL is set)',
    ]


def test_an_invalid_flag_is_still_rejected_when_nothing_overrides_it():
    with pytest.raises(ExporterError, match='TEAMSPEAK_PORT'):
        resolve(['--ts3port', '70000'])


def test_equal_values_in_different_spelling_are_not_reported():
    args = parse_args(['--pollinterval', '5', '--loglevel', 'info'])

    assert (
        overridden_flags(args, {'TEAMSPEAK_POLL_INTERVAL': '5.0', 'LOG_LEVEL': 'INFO'})
        == []
    )


def test_the_settings_banner_never_contains_the_password(caplog):
    caplog.set_level('INFO', logger='teamspeak_prometheus')

    log_settings(resolve(TEAMSPEAK_PASSWORD='hunter2'))

    assert 'hunter2' not in caplog.text
    assert 'Password: *censored*' in caplog.text


# -- empty values ---------------------------------------------------------------


@pytest.mark.parametrize('variable', ['TEAMSPEAK_HOST', 'TEAMSPEAK_USERNAME'])
@pytest.mark.parametrize('value', ['', ' ', '\t'], ids=['empty', 'space', 'tab'])
def test_an_empty_host_or_username_is_rejected(variable, value):
    # docker-compose's "VAR:" with no value sets an empty variable
    with pytest.raises(ExporterError, match=f'{variable} .* must not be empty'):
        resolve(**{variable: value})


@pytest.mark.parametrize('flag', ['--ts3host', '--ts3username'])
def test_an_empty_host_or_username_flag_is_rejected(flag):
    with pytest.raises(ExporterError, match='must not be empty'):
        resolve([flag, ''])


def test_an_empty_variable_overriding_a_good_flag_is_rejected():
    # environment variables win, also when empty: that is a mistake to report,
    # not to paper over with the flag
    with pytest.raises(ExporterError, match='TEAMSPEAK_HOST'):
        resolve(['--ts3host', 'ts.example.com'], TEAMSPEAK_HOST='')


def test_an_empty_flag_overridden_by_a_good_variable_is_only_reported():
    args = parse_args(['--ts3host', ''])
    env = {'TEAMSPEAK_HOST': 'ts.example.com'}

    assert resolve_config(args, env).host == 'ts.example.com'
    assert overridden_flags(args, env) == ['--ts3host (TEAMSPEAK_HOST is set)']


def test_an_empty_password_is_still_allowed():
    # it is the default: a ServerQuery login without a password
    assert resolve(TEAMSPEAK_PASSWORD='').password == ''
