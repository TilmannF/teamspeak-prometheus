"""Configuration: defaults, one parser per option, environment precedence."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from teamspeak_prometheus.errors import ExporterError
from teamspeak_prometheus.logs import log

DEFAULT_TS3_HOST = 'localhost'
DEFAULT_TS3_PORT = 10011
DEFAULT_TS3_USERNAME = 'serveradmin'
DEFAULT_TS3_PASSWORD = ''
DEFAULT_METRICS_PORT = 8000
DEFAULT_POLL_INTERVAL_IN_SECONDS = 5.0
DEFAULT_LOG_LEVEL = 'INFO'
LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')

# Upper bound for the poll interval. Anything slower is not monitoring, and far
# larger values overflow time.sleep().
MAX_POLL_INTERVAL_IN_SECONDS = 86400.0
# Lower bound. Faster is not monitoring, and one poll is at least five
# ServerQuery commands: even 1s trips TeamSpeak's default flood protection
# (10 commands per 3s) unless the exporter's IP is on the allowlist.
MIN_POLL_INTERVAL_IN_SECONDS = 1.0


@dataclass(frozen=True)
class Config:
    """Resolved exporter configuration."""

    host: str
    port: int
    username: str
    password: str
    metrics_port: int
    poll_interval: float = DEFAULT_POLL_INTERVAL_IN_SECONDS
    log_level: str = DEFAULT_LOG_LEVEL


def resolve_config(args: argparse.Namespace, env: Mapping[str, str]) -> Config:
    """Combine parsed arguments with the environment.

    Environment variables win over command-line arguments, which is the
    behavior this exporter has always had.
    """

    values = {}
    for dest, variable, default, field, parse in _OPTIONS:
        flag = getattr(args, dest, None)
        raw = env.get(variable, default if flag is None else flag)
        values[field] = parse(f'{variable} (--{dest})', raw)
    return Config(**values)  # type: ignore[arg-type]


def overridden_flags(args: argparse.Namespace, env: Mapping[str, str]) -> list[str]:
    """Flags that were passed explicitly but lose to a different env value.

    Only the overridden flag itself is checked: a flag the environment
    overrides is ignored, so it being invalid is reported, not fatal.
    """

    overridden = []
    for dest, variable, _, _, parse in _OPTIONS:
        flag = getattr(args, dest, None)
        if flag is None or variable not in env:
            continue
        try:
            differs = parse(variable, flag) != parse(variable, env[variable])
        except ExporterError:
            differs = True
        if differs:
            overridden.append(f'--{dest} ({variable} is set)')
    return overridden


def _port(name: str, value: object) -> int:
    try:
        port = int(str(value))
    except ValueError as err:
        raise ExporterError(f'{name} must be a port number, got {value!r}') from err
    if not 0 < port < 65536:
        raise ExporterError(f'{name} must be between 1 and 65535, got {port}')
    return port


def _interval(name: str, value: object) -> float:
    try:
        interval = float(str(value))
    except ValueError as err:
        raise ExporterError(
            f'{name} must be a number of seconds, got {value!r}'
        ) from err
    if not MIN_POLL_INTERVAL_IN_SECONDS <= interval <= MAX_POLL_INTERVAL_IN_SECONDS:
        raise ExporterError(
            f'{name} must be at least {MIN_POLL_INTERVAL_IN_SECONDS:g} and at most '
            f'{MAX_POLL_INTERVAL_IN_SECONDS:g} seconds, got {value!r}'
        )
    return interval


def _log_level(name: str, value: object) -> str:
    level = str(value).upper()
    if level not in LOG_LEVELS:
        raise ExporterError(
            f'{name} must be one of {", ".join(LOG_LEVELS)}, got {value!r}'
        )
    return level


def _text(name: str, value: object) -> str:
    return str(value)


def _required_text(name: str, value: object) -> str:
    """Text that must not be empty: an empty host silently meant localhost,
    an empty username a misleading "login rejected". docker-compose's
    ``VAR:`` with no value sets exactly that."""

    text = str(value)
    if not text.strip():
        raise ExporterError(f'{name} must not be empty')
    return text


# Every option: (argparse dest, environment variable, default, Config field,
# parser). A parser turns a raw flag or environment value into the Config value
# and raises ExporterError, naming the variable, when it is invalid.
_OPTIONS: list[tuple[str, str, object, str, Callable[[str, object], object]]] = [
    ('ts3host', 'TEAMSPEAK_HOST', DEFAULT_TS3_HOST, 'host', _required_text),
    ('ts3port', 'TEAMSPEAK_PORT', DEFAULT_TS3_PORT, 'port', _port),
    (
        'ts3username',
        'TEAMSPEAK_USERNAME',
        DEFAULT_TS3_USERNAME,
        'username',
        _required_text,
    ),
    ('ts3password', 'TEAMSPEAK_PASSWORD', DEFAULT_TS3_PASSWORD, 'password', _text),
    ('metricsport', 'METRICS_PORT', DEFAULT_METRICS_PORT, 'metrics_port', _port),
    (
        'pollinterval',
        'TEAMSPEAK_POLL_INTERVAL',
        DEFAULT_POLL_INTERVAL_IN_SECONDS,
        'poll_interval',
        _interval,
    ),
    ('loglevel', 'LOG_LEVEL', DEFAULT_LOG_LEVEL, 'log_level', _log_level),
]


# The startup banner. A fixed template: the configured values are logging
# arguments, so the filter escapes them -- a host with a line break in it cannot
# add lines to the log. The password is never among them.
SETTINGS_BANNER = (
    'TS3 SETTINGS:\nHost: %s\nPort: %s\nUsername: %s\nPassword: *censored*\n'
    'Poll interval: %gs'
)


def settings_arguments(config: Config) -> tuple[str, int, str, float]:
    return (config.host, config.port, config.username, config.poll_interval)


def log_settings(config: Config) -> None:
    log.info(SETTINGS_BANNER, *settings_arguments(config))
