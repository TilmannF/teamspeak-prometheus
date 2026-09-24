"""TeamSpeak 3 ServerQuery metrics exporter for Prometheus.

The module is importable without side effects: argument parsing, the metrics
HTTP server, and the polling loop all live behind ``main()``. See AGENTS.md.
"""

from __future__ import annotations

import argparse
import enum
import logging
import math
import os
import re
import signal
import socket
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import NoReturn, Protocol

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Info,
    start_http_server,
)

__version__ = '1.0.0'

METRICS_PREFIX = 'teamspeak_'
EXPORTER_PREFIX = 'teamspeak_exporter_'
VIRTUALSERVER_LABEL = 'virtualserver_name'

DEFAULT_TS3_HOST = 'localhost'
DEFAULT_TS3_PORT = 10011
DEFAULT_TS3_USERNAME = 'serveradmin'
DEFAULT_TS3_PASSWORD = ''
DEFAULT_METRICS_PORT = 8000
DEFAULT_POLL_INTERVAL_IN_SECONDS = 5.0
DEFAULT_LOG_LEVEL = 'INFO'
LOG_LEVELS = ('DEBUG', 'INFO', 'WARNING', 'ERROR')

# Socket timeout for every ServerQuery read and write. Without one, a server
# that stops answering mid-poll would stall the exporter forever.
SERVERQUERY_TIMEOUT_IN_SECONDS = 10.0
# Deadline for one whole ServerQuery session. The socket timeout alone does not
# bound a poll: a server trickling a byte at a time, or sending notifications
# without end, never lets a single read time out.
POLL_TIMEOUT_IN_SECONDS = 60.0
# Longest response line accepted. A real serverinfo line is about 4 KiB and a
# serverlist line about 300 bytes per virtualserver.
MAX_LINE_BYTES = 1024 * 1024
# After this many consecutive failed polls the wait between attempts stops
# growing. Never shorter than the configured interval.
MAX_BACKOFF_IN_SECONDS = 60.0
# Upper bound for the poll interval. Anything slower is not monitoring, and far
# larger values overflow time.sleep().
MAX_POLL_INTERVAL_IN_SECONDS = 86400.0
# Lower bound. Faster is not monitoring, and one poll is at least five
# ServerQuery commands: even 1s trips TeamSpeak's default flood protection
# (10 commands per 3s) unless the exporter's IP is on the allowlist.
MIN_POLL_INTERVAL_IN_SECONDS = 1.0
# TeamSpeak throttles query clients that are not on its allowlist (default: 10
# commands per 3 seconds) and answers ``error id=524``. The exporter waits as
# told and retries the command this many times before giving up on it.
FLOOD_RETRIES = 3
MAX_FLOOD_WAIT_IN_SECONDS = 10.0

ERROR_REASONS = ('connection', 'login', 'query', 'unexpected')

# Public API. Renaming or removing an entry breaks every dashboard and alerting
# rule built on this exporter. See AGENTS.md, "The Metric Contract".
METRICS_NAMES = [
    'connection_bandwidth_received_last_minute_total',
    'connection_bandwidth_received_last_second_total',
    'connection_bandwidth_sent_last_minute_total',
    'connection_bandwidth_sent_last_second_total',
    'connection_bytes_received_control',
    'connection_bytes_received_keepalive',
    'connection_bytes_received_speech',
    'connection_bytes_received_total',
    'connection_bytes_sent_control',
    'connection_bytes_sent_keepalive',
    'connection_bytes_sent_speech',
    'connection_bytes_sent_total',
    'connection_filetransfer_bandwidth_received',
    'connection_filetransfer_bandwidth_sent',
    'connection_filetransfer_bytes_received_total',
    'connection_filetransfer_bytes_sent_total',
    'connection_packets_received_control',
    'connection_packets_received_keepalive',
    'connection_packets_received_speech',
    'connection_packets_received_total',
    'connection_packets_sent_control',
    'connection_packets_sent_keepalive',
    'connection_packets_sent_speech',
    'connection_packets_sent_total',
    'virtualserver_channelsonline',
    'virtualserver_client_connections',
    'virtualserver_clientsonline',
    'virtualserver_maxclients',
    'virtualserver_month_bytes_downloaded',
    'virtualserver_month_bytes_uploaded',
    'virtualserver_query_client_connections',
    'virtualserver_queryclientsonline',
    'virtualserver_reserved_slots',
    'virtualserver_total_bytes_downloaded',
    'virtualserver_total_bytes_uploaded',
    'virtualserver_total_packetloss_control',
    'virtualserver_total_packetloss_keepalive',
    'virtualserver_total_packetloss_speech',
    'virtualserver_total_packetloss_total',
    'virtualserver_total_ping',
    'virtualserver_uptime',
]

log = logging.getLogger('teamspeak_prometheus')


class ExporterError(Exception):
    """Base class for errors raised by this exporter."""


class ServerQueryError(ExporterError):
    """ServerQuery answered a command with a non-zero error id.

    The message names the command, never its parameters: ``login`` carries the
    password.
    """

    def __init__(self, command: str, error_id: int, message: str) -> None:
        super().__init__(f'{command} failed: error id={error_id} {message}')
        self.command = command
        self.error_id = error_id
        self.message = message


class LoginFailed(ServerQueryError):
    """TeamSpeak rejected the ServerQuery credentials (error id 520)."""


# ---------------------------------------------------------------------------
# ServerQuery wire protocol
# ---------------------------------------------------------------------------

LINE_TERMINATOR = b'\n\r'
INVALID_LOGIN_ERROR_ID = 520  # "invalid loginname or password"
FLOOD_ERROR_ID = 524

_ESCAPES = [
    ('\\', r'\\'),
    ('/', r'\/'),
    (' ', r'\s'),
    ('|', r'\p'),
    ('\a', r'\a'),
    ('\b', r'\b'),
    ('\f', r'\f'),
    ('\n', r'\n'),
    ('\r', r'\r'),
    ('\t', r'\t'),
    ('\v', r'\v'),
]
_UNESCAPES = {escaped[1]: raw for raw, escaped in _ESCAPES}
_FLOOD_WAIT = re.compile(r'wait (\d+) second')


def escape(value: object) -> str:
    text = str(value)
    for raw, escaped in _ESCAPES:
        text = text.replace(raw, escaped)
    return text


def unescape(value: str) -> str:
    """Decode escape sequences in a single left-to-right pass.

    Sequential ``str.replace`` calls would re-examine text they just produced
    and decode an escaped backslash followed by an escape letter twice.
    """

    out: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character == '\\' and index + 1 < len(value):
            replacement = _UNESCAPES.get(value[index + 1])
            if replacement is not None:
                out.append(replacement)
                index += 2
                continue
        out.append(character)
        index += 1
    return ''.join(out)


def decode_record(payload: str) -> dict[str, str | None]:
    """Parse one ``key=value key=value`` record."""

    record: dict[str, str | None] = {}
    for chunk in payload.strip().split(' '):
        if not chunk:
            continue
        key, separator, value = chunk.partition('=')
        record[key] = unescape(value) if separator else None
    return record


class Connection(Protocol):
    """The part of ``socket.socket`` the client uses."""

    def sendall(self, data: bytes, /) -> None: ...

    def recv(self, bufsize: int, /) -> bytes: ...

    def close(self) -> None: ...


class ServerQueryClient:
    """A read-only TeamSpeak 3 ServerQuery client over the raw protocol.

    Sends one command at a time and waits for its ``error id=... msg=...``
    trailer. Replaces the archived ``python-ts3`` package, which needed
    ``telnetlib`` and cannot run on Python 3.13 or newer.
    """

    def __init__(
        self,
        connection: Connection,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        timeout: float = POLL_TIMEOUT_IN_SECONDS,
    ) -> None:
        self._connection = connection
        self._sleep = sleep
        self._clock = clock
        self._timeout = timeout
        self._deadline = clock() + timeout
        self._buffer = b''
        # The server greets with two lines: ``TS3`` and a welcome text. A server
        # that has banned this IP (flooding, failed logins) or reached its
        # per-IP connection limit closes the connection without a greeting.
        try:
            greeting = self._read_line()
        except ConnectionError as err:
            raise ConnectionError(
                'ServerQuery closed the connection before greeting; this IP may '
                'be banned for flooding or failed logins, see query_ip_allowlist'
            ) from err
        if greeting != 'TS3':
            raise ConnectionError('not a TeamSpeak 3 ServerQuery interface')
        self._read_line()

    @classmethod
    def connect(
        cls, host: str, port: int, session_timeout: float = POLL_TIMEOUT_IN_SECONDS
    ) -> ServerQueryClient:
        connection = socket.create_connection(
            (host, port), timeout=SERVERQUERY_TIMEOUT_IN_SECONDS
        )
        try:
            return cls(connection, timeout=session_timeout)
        except BaseException:
            connection.close()
            raise

    def login(self, username: str, password: str) -> None:
        """Log in. Only a credential rejection raises ``LoginFailed``; any other
        error -- flooding past the retry budget, a ban, a missing permission --
        stays a ``ServerQueryError``, so it is not reported as a bad password."""

        try:
            self.command(
                'login',
                client_login_name=username,
                client_login_password=password,
            )
        except ServerQueryError as err:
            if err.error_id != INVALID_LOGIN_ERROR_ID:
                raise
            raise LoginFailed(err.command, err.error_id, err.message) from None

    def serverlist(self) -> list[dict[str, str | None]]:
        return self.command('serverlist')

    def use(self, virtualserver_id: object) -> None:
        self.command('use', sid=virtualserver_id)

    def serverinfo(self) -> dict[str, str | None]:
        records = self.command('serverinfo')
        return records[0] if records else {}

    def close(self) -> None:
        try:
            self._send('quit')
        except OSError:
            pass
        finally:
            self._connection.close()

    def command(self, command: str, **params: object) -> list[dict[str, str | None]]:
        """Run one command; retry it when the server reports flooding."""

        for attempt in range(FLOOD_RETRIES + 1):
            self._send(command, params)
            records, error = self._read_response()
            error_id = _error_id(command, error)
            if error_id == 0:
                return records
            if error_id == FLOOD_ERROR_ID and attempt < FLOOD_RETRIES:
                wait = _flood_wait(error.get('extra_msg'))
                if self._clock() + wait >= self._deadline:
                    raise self._timed_out()
                log.debug('ServerQuery flood protection: waiting %.0fs', wait)
                self._sleep(wait)
                continue
            raise ServerQueryError(command, error_id, error.get('msg') or '')
        raise AssertionError('unreachable')  # pragma: no cover

    def _send(self, command: str, params: Mapping[str, object] | None = None) -> None:
        parts = [command]
        parts.extend(f'{key}={escape(value)}' for key, value in (params or {}).items())
        self._connection.sendall(' '.join(parts).encode('utf-8') + LINE_TERMINATOR)

    def _read_response(
        self,
    ) -> tuple[list[dict[str, str | None]], dict[str, str | None]]:
        records: list[dict[str, str | None]] = []
        while True:
            line = self._read_line()
            if line.startswith('error '):
                return records, decode_record(line[len('error ') :])
            if line.startswith('notify'):
                continue
            records.extend(decode_record(part) for part in line.split('|'))

    def _read_line(self) -> str:
        while LINE_TERMINATOR not in self._buffer:
            if len(self._buffer) > MAX_LINE_BYTES:
                raise ConnectionError(
                    f'ServerQuery sent a line longer than {MAX_LINE_BYTES} bytes'
                )
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                raise self._timed_out()
            # Never wait on one read past the session deadline.
            settimeout = getattr(self._connection, 'settimeout', None)
            if settimeout is not None:
                settimeout(min(remaining, SERVERQUERY_TIMEOUT_IN_SECONDS))
            chunk = self._connection.recv(4096)
            if not chunk:
                raise ConnectionError('ServerQuery closed the connection')
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(LINE_TERMINATOR)
        return line.decode('utf-8', errors='replace')

    def _timed_out(self) -> TimeoutError:
        return TimeoutError(
            f'ServerQuery session did not finish within {self._timeout:g}s'
        )


def _error_id(command: str, error: Mapping[str, str | None]) -> int:
    """The numeric id of an ``error`` trailer.

    A trailer without one is a protocol violation, not success: treating it as
    id 0 would turn a garbled ``serverlist`` into "no virtualservers" and drop
    every series.
    """

    try:
        return int(error.get('id') or '')
    except ValueError:
        raise ServerQueryError(
            command, -1, 'malformed response: error line without a numeric id'
        ) from None


def _flood_wait(extra_message: str | None) -> float:
    match = _FLOOD_WAIT.search(extra_message or '')
    wait = float(match.group(1)) if match else 1.0
    return min(max(wait, 1.0), MAX_FLOOD_WAIT_IN_SECONDS)


class Ts3Client(Protocol):
    """The client surface the metric service uses. See ``ServerQueryClient``."""

    def login(self, username: str, password: str) -> None: ...

    def serverlist(self) -> list[dict[str, str | None]]: ...

    def use(self, virtualserver_id: object) -> None: ...

    def serverinfo(self) -> dict[str, str | None]: ...

    def close(self) -> None: ...


ClientFactory = Callable[[str, int], Ts3Client]


def default_client_factory(host: str, port: int) -> Ts3Client:
    return ServerQueryClient.connect(host, port)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


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


class SafeArgumentParser(argparse.ArgumentParser):
    """An ``ArgumentParser`` whose error messages never repeat a value.

    Every command-line parser in this repository uses it -- the exporter, the
    test harness, the fake server -- and tests/test_cli.py fails on any other.

    argparse quotes command-line fragments in its errors -- ``unrecognized
    arguments: --ts3pasword <password>`` after a typo, ``ambiguous option:
    --ts3=<password>`` -- and prints them to stderr, where no logging filter
    sees them. Only option strings this parser defines stay visible; every
    other token is replaced by ``…``, and so is anything after ``=``. That
    includes tokens starting with ``-``: an unknown ``--ts3pasword`` and a
    password ``-secret`` look alike, so neither can be shown.
    """

    _argv: list[str] = []

    def parse_known_args(self, args=None, namespace=None):  # type: ignore[override]
        self._argv = list(sys.argv[1:] if args is None else args)
        return super().parse_known_args(args, namespace)

    def error(self, message: str) -> NoReturn:
        super().error(
            _mask_values(message, self._argv, set(self._option_string_actions))
        )


def _mask_values(message: str, argv: list[str], known: set[str]) -> str:
    """Replace every command-line fragment except known option strings."""

    fragments = set()
    for token in argv:
        name, separator, value = token.partition('=')
        if token in known:
            continue
        if separator and name in known:
            if value:
                fragments.add(value)
            continue
        if token:
            fragments.add(token)
    masked = message
    for fragment in sorted(fragments, key=len, reverse=True):
        # Whole fragments only: a value "3" must not mangle "--ts3port".
        pattern = r'(?<![^\s\'"=])' + re.escape(fragment) + r'(?![^\s\'"])'
        masked = re.sub(pattern, '…', masked)
    if masked != message:
        masked += ' (argument values hidden; see --help)'
    return masked


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse flags as plain strings.

    Nothing is converted or validated here: environment variables take
    precedence over flags, so a flag is checked only if it is actually used --
    by ``resolve_config``, with the same parser as the environment variable.
    Unset flags stay ``None`` so that precedence can be traced.
    """

    parser = SafeArgumentParser()
    parser.add_argument(
        '--ts3host',
        help=f'Hostname or ip address of TS3 server (default: {DEFAULT_TS3_HOST})',
    )
    parser.add_argument(
        '--ts3port',
        help=f'Port of TS3 server (default: {DEFAULT_TS3_PORT})',
    )
    parser.add_argument(
        '--ts3username',
        help=f'ServerQuery username of TS3 server (default: {DEFAULT_TS3_USERNAME})',
    )
    parser.add_argument(
        '--ts3password',
        help='ServerQuery password of TS3 server. Prefer TEAMSPEAK_PASSWORD: '
        'flags are visible in the process list',
    )
    parser.add_argument(
        '--metricsport',
        help='Port on which this service exposes the metrics '
        f'(default: {DEFAULT_METRICS_PORT})',
    )
    parser.add_argument(
        '--pollinterval',
        help='Seconds between two polls of the TS3 server, '
        f'{MIN_POLL_INTERVAL_IN_SECONDS:g} to {MAX_POLL_INTERVAL_IN_SECONDS:g} '
        f'(default: {DEFAULT_POLL_INTERVAL_IN_SECONDS:g})',
    )
    parser.add_argument(
        '--loglevel',
        help=f'Log level: {", ".join(LOG_LEVELS)} (default: {DEFAULT_LOG_LEVEL})',
    )
    return parser.parse_args(argv)


def password_candidates(args: argparse.Namespace, env: Mapping[str, str]) -> list[str]:
    """Every password given, used or not: an overridden one is a secret too."""

    return [
        value
        for value in (getattr(args, 'ts3password', None), env.get('TEAMSPEAK_PASSWORD'))
        if value
    ]


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


# Every option: (argparse dest, environment variable, default, Config field,
# parser). A parser turns a raw flag or environment value into the Config value
# and raises ExporterError, naming the variable, when it is invalid.
_OPTIONS: list[tuple[str, str, object, str, Callable[[str, object], object]]] = [
    ('ts3host', 'TEAMSPEAK_HOST', DEFAULT_TS3_HOST, 'host', _text),
    ('ts3port', 'TEAMSPEAK_PORT', DEFAULT_TS3_PORT, 'port', _port),
    ('ts3username', 'TEAMSPEAK_USERNAME', DEFAULT_TS3_USERNAME, 'username', _text),
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


def describe_settings(config: Config) -> str:
    """Render the startup banner. The password is never included."""

    return (
        f'TS3 SETTINGS:\nHost: {config.host}\nPort: {config.port}\n'
        f'Username: {config.username}\nPassword: *censored*\n'
        f'Poll interval: {config.poll_interval:g}s'
    )


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def build_gauges(registry: CollectorRegistry) -> dict[str, Gauge]:
    """Create one labelled gauge per TeamSpeak metric.

    The registry is explicit because building twice against the global default
    registry raises ``Duplicated timeseries``, which would break the tests.
    """

    gauges: dict[str, Gauge] = {}
    for teamspeak_metric_name in METRICS_NAMES:
        gauges[teamspeak_metric_name] = Gauge(
            METRICS_PREFIX + teamspeak_metric_name,
            METRICS_PREFIX + teamspeak_metric_name,
            [VIRTUALSERVER_LABEL],
            registry=registry,
        )
        log.debug('Initialized gauge %s', teamspeak_metric_name)
    return gauges


@dataclass(frozen=True)
class ExporterMetrics:
    """The exporter's own health, next to the TeamSpeak passthrough gauges."""

    last_poll_timestamp: Gauge
    last_successful_poll_timestamp: Gauge
    poll_success: Gauge
    poll_duration: Gauge
    poll_errors: Counter
    missing_fields: Gauge


def build_exporter_metrics(registry: CollectorRegistry) -> ExporterMetrics:
    Info(
        EXPORTER_PREFIX + 'build',
        'Version of teamspeak-prometheus',
        registry=registry,
    ).info({'version': __version__})
    poll_errors = Counter(
        EXPORTER_PREFIX + 'poll_errors',
        'Failed ServerQuery polls or poll steps, by reason',
        ['reason'],
        registry=registry,
    )
    for reason in ERROR_REASONS:
        poll_errors.labels(reason=reason)
    return ExporterMetrics(
        last_poll_timestamp=Gauge(
            EXPORTER_PREFIX + 'last_poll_timestamp_seconds',
            'Unix time the last poll started',
            registry=registry,
        ),
        last_successful_poll_timestamp=Gauge(
            EXPORTER_PREFIX + 'last_successful_poll_timestamp_seconds',
            'Unix time the last fully successful poll started',
            registry=registry,
        ),
        poll_success=Gauge(
            EXPORTER_PREFIX + 'poll_success',
            '1 if the last poll read every virtualserver without error, else 0',
            registry=registry,
        ),
        poll_duration=Gauge(
            EXPORTER_PREFIX + 'poll_duration_seconds',
            'Duration of the last poll',
            registry=registry,
        ),
        poll_errors=poll_errors,
        missing_fields=Gauge(
            EXPORTER_PREFIX + 'missing_fields',
            'Expected serverinfo fields that were missing or not numeric in the '
            'last poll; their teamspeak_* series are not exported',
            [VIRTUALSERVER_LABEL],
            registry=registry,
        ),
    )


def update_gauges(
    gauges: Mapping[str, Gauge],
    virtualserver_name: str,
    serverinfo: Mapping[str, object],
) -> list[str]:
    """Copy one ``serverinfo`` response into the gauges, unconverted.

    A field that is missing or not a number is skipped, and its series removed
    so a stale value does not linger. Returns the skipped field names.
    """

    skipped = []
    for teamspeak_metric_name in METRICS_NAMES:
        gauge = gauges[teamspeak_metric_name]
        try:
            value = float(serverinfo[teamspeak_metric_name])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            skipped.append(teamspeak_metric_name)
            _remove_series(gauge, virtualserver_name)
            continue
        gauge.labels(**{VIRTUALSERVER_LABEL: virtualserver_name}).set(value)
    return skipped


def _remove_series(metric: Gauge, virtualserver_name: str) -> None:
    with suppress(KeyError):
        metric.remove(virtualserver_name)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

REDACTED = '*censored*'


def secrets_for_redaction(secrets: list[str]) -> list[str]:
    """Non-empty and longest first, so a secret containing another is
    replaced whole."""

    return sorted({s for s in secrets if s}, key=len, reverse=True)


def redact(text: str, secrets: list[str]) -> str:
    """Replace every occurrence of each secret with ``*censored*``.

    ``secrets`` must come from ``secrets_for_redaction``.
    """

    for secret in secrets:
        text = text.replace(secret, REDACTED)
    return text


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


class PollResult(enum.Enum):
    OK = 'ok'
    PARTIAL = 'partial'  # reached the server; some virtualservers failed
    FAILED = 'failed'  # could not connect, log in, or list virtualservers


class Teamspeak3MetricService:
    """Reads ``serverinfo`` for every virtualserver and updates the gauges.

    ``poll()`` never raises for anything that can go wrong on the network or
    in the server's answers: it logs, counts the error, and reports a result.
    """

    def __init__(
        self,
        config: Config,
        gauges: Mapping[str, Gauge],
        exporter_metrics: ExporterMetrics,
        client_factory: ClientFactory = default_client_factory,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.gauges = gauges
        self.exporter_metrics = exporter_metrics
        self.client_factory = client_factory
        self.clock = clock
        self.known_virtualservers: set[str] = set()
        self._warned_missing: set[tuple[str, str]] = set()
        # Label values come from the server and are untrusted, like its log
        # text: a hostile server could name a virtualserver after the password
        # it was just sent, and /metrics is unauthenticated.
        self._secrets = secrets_for_redaction([config.password])
        # virtualserver id -> status, for those serverlist reports not online
        self._not_online: dict[str, str] = {}
        # names shared by several virtualservers, already warned about
        self._warned_duplicates: set[str] = set()

    def poll(self) -> PollResult:
        metrics = self.exporter_metrics
        started = self.clock()
        metrics.last_poll_timestamp.set(started)
        try:
            result = self._poll()
        except LoginFailed as err:
            result = self._failed('login', 'ServerQuery login rejected: %s', err)
        except ServerQueryError as err:
            result = self._failed('query', 'ServerQuery error: %s', err)
        except OSError as err:
            result = self._failed(
                'connection',
                'Cannot reach ServerQuery at %s:%s: %s',
                self.config.host,
                self.config.port,
                err,
            )
        except Exception:
            log.exception('Unexpected error during poll')
            metrics.poll_errors.labels(reason='unexpected').inc()
            result = PollResult.FAILED

        metrics.poll_duration.set(max(self.clock() - started, 0.0))
        metrics.poll_success.set(1 if result is PollResult.OK else 0)
        if result is PollResult.OK:
            metrics.last_successful_poll_timestamp.set(started)
        return result

    def _failed(self, reason: str, message: str, *args: object) -> PollResult:
        log.error(message, *args)
        self.exporter_metrics.poll_errors.labels(reason=reason).inc()
        return PollResult.FAILED

    def _poll(self) -> PollResult:
        log.debug('Fetching metrics')
        client = self.client_factory(self.config.host, self.config.port)
        try:
            client.login(self.config.username, self.config.password)
            servers = client.serverlist()
            result = PollResult.OK
            seen: set[str] = set()
            not_online: dict[str, str] = {}
            owners: dict[str, list[str]] = {}
            for server in servers:
                virtualserver_id = server.get('virtualserver_id')
                status = server.get('virtualserver_status')
                if status not in (None, 'online'):
                    # Stopped (or booting, deploying, ...) is a state of the
                    # host, not an error of the exporter: skip it silently.
                    not_online[str(virtualserver_id)] = str(status)
                    continue
                try:
                    client.use(virtualserver_id)
                    serverinfo = client.serverinfo()
                except ServerQueryError as err:
                    log.warning('Skipping virtualserver %s: %s', virtualserver_id, err)
                    self.exporter_metrics.poll_errors.labels(reason='query').inc()
                    result = PollResult.PARTIAL
                    continue
                name = redact(
                    str(
                        serverinfo.get(VIRTUALSERVER_LABEL)
                        or server.get(VIRTUALSERVER_LABEL)
                        or f'virtualserver {virtualserver_id}'
                    ),
                    self._secrets,
                )
                self._record(name, serverinfo)
                seen.add(name)
                owners.setdefault(name, []).append(str(virtualserver_id))
            self._report_duplicates(owners)
            self._report_status_changes(not_online, servers)
            self._forget(self.known_virtualservers - seen)
            self.known_virtualservers = seen
            return result
        finally:
            client.close()

    def _report_duplicates(self, owners: dict[str, list[str]]) -> None:
        """Warn once per name shared by several virtualservers.

        ``virtualserver_name`` is the only label, so they write the same series
        and the last one read wins. Telling them apart needs a
        ``virtualserver_id`` label -- a breaking change, see
        docs/modernization-backlog.md.
        """

        duplicates = {name: ids for name, ids in owners.items() if len(ids) > 1}
        for name, ids in duplicates.items():
            if name not in self._warned_duplicates:
                log.warning(
                    "Virtualservers %s are all named '%s'; they share one set of "
                    'series and only the last one read is exported. Give them '
                    'distinct names.',
                    ', '.join(ids),
                    name,
                )
        self._warned_duplicates = set(duplicates)

    def _report_status_changes(
        self, not_online: dict[str, str], servers: list[dict[str, str | None]]
    ) -> None:
        """Log once when a virtualserver stops or starts being online."""

        names = {
            str(server.get('virtualserver_id')): server.get(VIRTUALSERVER_LABEL)
            for server in servers
        }
        for virtualserver_id, status in not_online.items():
            if self._not_online.get(virtualserver_id) != status:
                log.info(
                    "Virtualserver %s '%s' is %s; not exporting it",
                    virtualserver_id,
                    names.get(virtualserver_id),
                    status,
                )
        for virtualserver_id in self._not_online.keys() - not_online.keys():
            if virtualserver_id in names:
                log.info(
                    "Virtualserver %s '%s' is online again",
                    virtualserver_id,
                    names[virtualserver_id],
                )
        self._not_online = not_online

    def _record(self, name: str, serverinfo: Mapping[str, object]) -> None:
        skipped = update_gauges(self.gauges, name, serverinfo)
        self.exporter_metrics.missing_fields.labels(**{VIRTUALSERVER_LABEL: name}).set(
            len(skipped)
        )
        for field in skipped:
            if (name, field) not in self._warned_missing:
                self._warned_missing.add((name, field))
                log.warning(
                    "Virtualserver '%s': serverinfo field %s missing or not numeric; "
                    'not exporting it',
                    name,
                    field,
                )

    def _forget(self, virtualservers: set[str]) -> None:
        """Drop every series of virtualservers no longer read: deleted, not
        online, or failed this poll."""

        for name in virtualservers:
            log.info("Virtualserver '%s' was not read; removing its series", name)
            for gauge in self.gauges.values():
                _remove_series(gauge, name)
            _remove_series(self.exporter_metrics.missing_fields, name)
            self._warned_missing = {
                entry for entry in self._warned_missing if entry[0] != name
            }


def next_delay(interval: float, consecutive_failures: int) -> float:
    """Wait before the next poll: the interval, doubled per failure, capped.

    ``interval`` must be positive. Doubling continues until the cap for any
    positive interval, however small: the number of doublings is bounded by the
    number actually needed to reach the cap, and ``ldexp`` scales by powers of
    two without an intermediate result that could overflow.
    """

    if consecutive_failures == 0:
        return interval
    cap = max(interval, MAX_BACKOFF_IN_SECONDS)
    # log2(cap) - log2(interval), not log2(cap / interval): the quotient
    # overflows to infinity for a subnormal interval.
    needed = math.ceil(math.log2(cap) - math.log2(interval))
    return min(math.ldexp(interval, min(consecutive_failures, needed)), cap)


def poll_forever(
    service: Teamspeak3MetricService,
    interval_in_seconds: float,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll every ``interval_in_seconds``, start to start — forever, or
    ``iterations`` times. Backs off while polls fail outright."""

    failures = 0
    remaining = iterations
    while remaining is None or remaining > 0:
        started = clock()
        result = service.poll()
        failures = failures + 1 if result is PollResult.FAILED else 0
        if remaining is not None:
            remaining -= 1
            if remaining == 0:
                return
        sleep(max(next_delay(interval_in_seconds, failures) - (clock() - started), 0))


# C0 and C1 control characters, DEL, and the Unicode line and paragraph
# separators some log viewers break lines on. Tab stays readable.
_CONTROL_CHARACTERS = re.compile(r'[\x00-\x08\x0a-\x1f\x7f-\x9f\u2028\u2029]')


class RedactingFilter(logging.Filter):
    """Keeps secrets and forged lines out of the exporter's log.

    Text from the TeamSpeak server -- error messages, virtualserver names --
    reaches the log as arguments of a log call and is untrusted: a hostile or
    compromised server could echo the password it was just sent, or embed line
    breaks to fake log lines. On every record this filter

    * replaces each secret with ``*censored*``: in the message, in its
      arguments, and in a traceback;
    * escapes control characters in string arguments, so one call is always one
      log line. The message template itself is the exporter's own text and may
      span lines (the settings banner does).

    Numbers pass through untouched so ``%d``/``%f`` formats keep working.
    """

    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = secrets_for_redaction(secrets)

    def redact(self, text: str) -> str:
        return redact(text, self.secrets)

    def clean(self, value: object) -> object:
        if isinstance(value, (int, float)):
            return value
        text = self.redact(str(value))
        return _CONTROL_CHARACTERS.sub(lambda match: repr(match.group())[1:-1], text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(str(record.msg))
        if isinstance(record.args, Mapping):
            record.args = {key: self.clean(v) for key, v in record.args.items()}
        elif record.args:
            record.args = tuple(self.clean(arg) for arg in record.args)
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self.redact(record.exc_text)
        return True


def configure_logging(level: str, secrets: list[str] | None = None) -> None:
    """Set up logging; ``secrets`` are redacted from every exporter log line."""

    logging.basicConfig(
        level=level, format='%(asctime)s %(levelname)s %(message)s', force=True
    )
    for existing in [f for f in log.filters if isinstance(f, RedactingFilter)]:
        log.removeFilter(existing)
    log.addFilter(RedactingFilter(secrets or []))


class Shutdown(BaseException):
    """SIGTERM arrived. A ``BaseException``, like ``KeyboardInterrupt``, so the
    ``except Exception`` in ``poll()`` cannot swallow it."""


def _shut_down(signum: int, frame: object) -> None:
    raise Shutdown


def handle_termination() -> None:
    """Stop cleanly on SIGTERM.

    In a container the exporter is PID 1, and the kernel ignores a signal PID 1
    has no handler for: without this, ``docker stop`` waits its 10 seconds and
    then kills the process (exit code 137). The exception interrupts a poll
    wherever it is, even a blocked socket read, and the ``finally`` blocks
    still close the ServerQuery session.
    """

    signal.signal(signal.SIGTERM, _shut_down)


def main(argv: list[str] | None = None) -> int:
    # Redaction first: configuration errors quote the offending value, which
    # can equal the password. Every password given counts, used or not.
    configure_logging(
        DEFAULT_LOG_LEVEL, secrets=password_candidates(argparse.Namespace(), os.environ)
    )
    args = parse_args(argv)
    secrets = password_candidates(args, os.environ)
    configure_logging(DEFAULT_LOG_LEVEL, secrets=secrets)
    try:
        config = resolve_config(args, os.environ)
    except ExporterError as err:
        log.error('Invalid configuration: %s', err)
        return 2
    configure_logging(config.log_level, secrets=secrets)
    for flag in overridden_flags(args, os.environ):
        log.warning('Ignoring %s: environment variables take precedence', flag)
    log.info(describe_settings(config))

    handle_termination()
    try:
        return serve(config)
    except (KeyboardInterrupt, Shutdown):
        log.info('Stopped')
        return 0


def serve(config: Config) -> int:
    """Expose the metrics and poll until interrupted."""

    gauges = build_gauges(REGISTRY)
    exporter_metrics = build_exporter_metrics(REGISTRY)
    try:
        start_http_server(config.metrics_port)
    except OSError as err:
        log.error('Cannot listen on port %s: %s', config.metrics_port, err)
        return 1
    log.info('Started metrics endpoint on port %s', config.metrics_port)

    poll_forever(
        Teamspeak3MetricService(config, gauges, exporter_metrics), config.poll_interval
    )
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
