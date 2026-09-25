"""A read-only TeamSpeak 3 ServerQuery client over the raw protocol."""

from __future__ import annotations

import re
import socket
import time
from collections.abc import Callable, Mapping
from typing import Protocol

from teamspeak_prometheus.errors import LoginFailed, ServerQueryError
from teamspeak_prometheus.logs import log
from teamspeak_prometheus.wire import LINE_TERMINATOR, decode_record, escape

# Socket timeout for every ServerQuery read and write. Without one, a server
# that stops answering mid-poll would stall the exporter forever.
SERVERQUERY_TIMEOUT_IN_SECONDS = 10.0
# Deadline for one whole ServerQuery session. The socket timeout alone does not
# bound a poll: a server trickling a byte at a time, or sending notifications
# without end, never lets a single read time out.
POLL_TIMEOUT_IN_SECONDS = 60.0
# Once serverlist shows how much work the session has, the budget grows by this
# much per online virtualserver: a host that is not on the allowlist is
# throttled to about 0.6s per virtualserver, so 150 of them need more than 60s.
PER_VIRTUALSERVER_TIMEOUT_IN_SECONDS = 5.0
# ... but never beyond this: a hostile server listing thousands of
# virtualservers must not buy itself hours.
MAX_SESSION_TIMEOUT_IN_SECONDS = 900.0
# Longest response line accepted. A real serverinfo line is about 4 KiB and a
# serverlist line about 300 bytes per virtualserver.
MAX_LINE_BYTES = 1024 * 1024

# TeamSpeak throttles query clients that are not on its allowlist (default: 10
# commands per 3 seconds) and answers ``error id=524``. The exporter waits as
# told and retries the command this many times before giving up on it.
FLOOD_RETRIES = 3
MAX_FLOOD_WAIT_IN_SECONDS = 10.0

INVALID_LOGIN_ERROR_ID = 520  # "invalid loginname or password"
FLOOD_ERROR_ID = 524

_FLOOD_WAIT = re.compile(r'wait (\d+) second')


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
        self._started = clock()
        self._deadline = self._started + timeout
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
        """List the virtualservers, and grow the session budget to fit them.

        Every online virtualserver adds ``PER_VIRTUALSERVER_TIMEOUT_IN_SECONDS``
        -- ones that are not online are skipped by the exporter and cost
        nothing -- up to ``MAX_SESSION_TIMEOUT_IN_SECONDS`` in total. Without
        this, a large throttled host ran out of budget part-way, every poll,
        and the virtualservers after that point were never read.
        """

        records = self.command('serverlist')
        online = sum(
            1
            for record in records
            if record.get('virtualserver_status') in (None, 'online')
        )
        self._deadline = min(
            self._deadline + online * PER_VIRTUALSERVER_TIMEOUT_IN_SECONDS,
            self._started + MAX_SESSION_TIMEOUT_IN_SECONDS,
        )
        return records

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
        budget = self._deadline - self._started
        return TimeoutError(f'ServerQuery session did not finish within {budget:g}s')


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
