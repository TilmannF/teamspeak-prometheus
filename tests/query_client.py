"""A small socket-based ServerQuery client used by the test harness.

The production exporter uses the archived ``ts3`` package, which depends on
``telnetlib`` and therefore cannot be installed on Python 3.13 or newer. This
client speaks the same wire protocol and exposes the same surface the exporter
uses, so the end-to-end smoke test runs on any supported Python.

It is test support, not production code. Replacing the archived package for
real is tracked in docs/modernization-backlog.md.
"""

from __future__ import annotations

import socket
from dataclasses import dataclass

from tests.serverquery import BANNER, LINE_TERMINATOR, decode_pairs, escape


class ServerQueryError(Exception):
    """The fake or real ServerQuery interface did not behave as expected."""


@dataclass
class QueryResponse:
    """Mirrors the shape of ``ts3.TS3Response`` that the exporter reads."""

    response: dict[str, str | None]
    data: list[dict[str, str | None]]


class QueryClient:
    """Implements the client surface declared by ``app.Ts3Client``."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self._socket = socket.create_connection((host, port), timeout=timeout)
        self._buffer = b''
        if not self._read_line().endswith(b'TS3'):
            raise ServerQueryError('missing %r banner' % BANNER)

    def login(self, username: str, password: str) -> bool:
        response = self.send_command(
            'login',
            {'client_login_name': username, 'client_login_password': password},
        )
        return response.response['msg'] == 'ok'

    def serverlist(self) -> QueryResponse:
        return self.send_command('serverlist')

    def use(self, virtualserver_id: object) -> QueryResponse:
        return self.send_command('use', {'sid': virtualserver_id})

    def send_command(
        self, command: str, keys: dict[str, object] | None = None
    ) -> QueryResponse:
        parts = [command]
        for key, value in (keys or {}).items():
            parts.append('%s=%s' % (key, escape(value)))
        self._socket.sendall(' '.join(parts).encode('utf-8') + LINE_TERMINATOR)

        line = self._read_line().decode('utf-8')
        data: list[dict[str, str | None]] = []
        if not line.startswith('error'):
            data = [decode_pairs(record) for record in line.split('|')]
            line = self._read_line().decode('utf-8')
        return QueryResponse(response=decode_pairs(line[len('error ') :]), data=data)

    def disconnect(self) -> None:
        try:
            self._socket.sendall(b'quit' + LINE_TERMINATOR)
        except OSError:
            pass
        finally:
            self._socket.close()

    def _read_line(self) -> bytes:
        while LINE_TERMINATOR not in self._buffer:
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ServerQueryError('connection closed while reading a response')
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(LINE_TERMINATOR)
        return line
