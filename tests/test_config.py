"""Configuration resolution: defaults, environment precedence, port parsing."""

from __future__ import annotations

import pytest

import app


def resolve(argv: list[str] | None = None, **env: str) -> app.Config:
    return app.resolve_config(app.parse_args(argv or []), env)


def test_defaults_match_the_documented_values():
    config = resolve()

    assert config == app.Config(
        host='localhost',
        port=10011,
        username='serveradmin',
        password='',
        metrics_port=8000,
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
        ]
    )

    assert config.host == 'example.com'
    assert config.port == 12345
    assert config.username == 'ExampleUser'
    assert config.password == 'SomePassword'
    assert config.metrics_port == 8080


def test_environment_overrides_command_line_arguments():
    config = resolve(
        ['--ts3host', 'from-flag', '--ts3username', 'from-flag'],
        TEAMSPEAK_HOST='from-env',
        TEAMSPEAK_USERNAME='env-user',
        TEAMSPEAK_PASSWORD='env-password',
    )

    assert config.host == 'from-env'
    assert config.username == 'env-user'
    assert config.password == 'env-password'


def test_ports_from_the_environment_are_integers():
    config = resolve(TEAMSPEAK_PORT='10022', METRICS_PORT='9000')

    assert config.port == 10022
    assert config.metrics_port == 9000


@pytest.mark.parametrize('variable', ['TEAMSPEAK_PORT', 'METRICS_PORT'])
def test_a_non_numeric_port_fails_with_a_clear_error(variable: str):
    with pytest.raises(app.ExporterError, match=variable):
        resolve(**{variable: 'not-a-port'})


def test_the_settings_banner_never_contains_the_password():
    banner = app.describe_settings(resolve(TEAMSPEAK_PASSWORD='hunter2'))

    assert 'hunter2' not in banner
    assert '*censored*' in banner
