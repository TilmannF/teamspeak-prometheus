"""A fake TeamSpeak 3 ServerQuery server.

It speaks enough of the real protocol -- two-line banner, ``\\n\\r``
terminators, escaping, ``error id=0 msg=ok`` trailers, flood protection -- that
the exporter cannot tell it from TeamSpeak 3.13. Captured responses of a real
server live in ``tests/fixtures`` and keep the two honest. It exists so this
repository can be developed and verified with no TeamSpeak server anywhere.
"""

from __future__ import annotations

import argparse
import math
import socketserver
import threading
import time
from collections import deque

from tests.serverquery import (
    BANNER,
    LINE_TERMINATOR,
    decode_pairs,
    encode_pairs,
    encode_records,
    escape,
)

# What a hostile server appends to its error messages: a forged log line.
FORGED_LOG_LINE = '2026-01-01 00:00:00,000 CRITICAL forged by the server'

DEFAULT_NAMES = ['Test Server', 'Zweiter Server']


def virtualservers(count: int) -> list[dict[str, object]]:
    names = DEFAULT_NAMES + [f'Server {n}' for n in range(3, count + 1)]
    return [
        {'virtualserver_id': sid, 'virtualserver_name': names[sid - 1]}
        for sid in range(1, count + 1)
    ]


VIRTUALSERVERS = virtualservers(2)


# Every metric this exporter reads, plus the name it labels them with. Values
# are arbitrary but distinct per virtualserver so tests can tell them apart.
def serverinfo(server: dict[str, object]) -> dict[str, object]:
    from app import METRICS_NAMES

    virtualserver_id = int(server['virtualserver_id'])  # type: ignore[arg-type]
    info = dict(server)
    for offset, metric in enumerate(METRICS_NAMES):
        info[metric] = virtualserver_id * 1000 + offset
    return info


class _FloodGuard:
    """TeamSpeak's per-client command budget: ``limit`` per ``window`` seconds."""

    def __init__(self, limit: int | None, window: float) -> None:
        self.limit = limit
        self.window = window
        self.accepted: deque[float] = deque()
        self.lock = threading.Lock()

    def wait_needed(self) -> int:
        """0 if the command may run, else whole seconds to wait."""

        if self.limit is None:
            return 0
        with self.lock:
            now = time.monotonic()
            while self.accepted and now - self.accepted[0] >= self.window:
                self.accepted.popleft()
            if len(self.accepted) >= self.limit:
                return max(1, math.ceil(self.accepted[0] + self.window - now))
            self.accepted.append(now)
            return 0


class _Handler(socketserver.StreamRequestHandler):
    server: _Server

    def handle(self) -> None:
        self.wfile.write(BANNER)
        self._write('Welcome to the fake TeamSpeak 3 ServerQuery interface.')
        selected = None
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command, _, arguments = line.decode('utf-8').strip().partition(' ')
            keys = decode_pairs(arguments)

            wait = self.server.flood.wait_needed()
            if wait:
                self._error(
                    524,
                    'client is flooding',
                    f'please wait {wait} seconds',
                )
                continue
            if command == 'quit':
                self._ok()
                return
            if command == 'login':
                submitted = keys.get('client_login_password') or ''
                if submitted == self.server.password:
                    self._ok()
                elif self.server.hostile:
                    self._error(520, f'invalid password {submitted}\n{FORGED_LOG_LINE}')
                else:
                    self._error(520, 'invalid loginname or password')
                continue
            if command == 'serverlist':
                self._data(encode_records(self.server.virtualservers))
                continue
            if command == 'use':
                selected = self._find(keys.get('sid'))
                if selected is None:
                    self._error(1024, 'invalid serverID')
                elif self.server.hostile and selected['virtualserver_id'] == 2:
                    # echoes the password it was sent at login
                    self._error(
                        1033,
                        f'server is not running {self.server.password}\n'
                        f'{FORGED_LOG_LINE}',
                    )
                    selected = None
                else:
                    self._ok()
                continue
            if command == 'serverinfo':
                if selected is None:
                    self._error(1024, 'invalid serverID')
                else:
                    self._data(encode_pairs(serverinfo(selected)))
                continue
            self._error(256, 'command not found')

    def _find(self, sid: str | None) -> dict[str, object] | None:
        return next(
            (
                s
                for s in self.server.virtualservers
                if str(s['virtualserver_id']) == sid
            ),
            None,
        )

    def _write(self, payload: str) -> None:
        self.wfile.write(payload.encode('utf-8') + LINE_TERMINATOR)

    def _ok(self) -> None:
        self._write('error id=0 msg=ok')

    def _error(self, code: int, message: str, extra: str | None = None) -> None:
        line = f'error id={code} msg={escape(message)}'
        if extra:
            line += f' extra_msg={escape(extra)}'
        self._write(line)

    def _data(self, payload: str) -> None:
        self._write(payload)
        self._ok()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    password = ''
    hostile = False
    virtualservers: list[dict[str, object]] = VIRTUALSERVERS
    flood = _FloodGuard(None, 3.0)


class FakeTs3Server:
    """Runs the fake ServerQuery interface on an ephemeral port.

    ``flood_limit`` enables TeamSpeak-style flood protection: at most that
    many commands per ``flood_window`` seconds, shared by all connections --
    TeamSpeak counts per client IP, and every test client is 127.0.0.1.

    ``hostile`` makes it behave like a compromised server: a rejected login and
    ``use`` of virtualserver 2 answer with error text that echoes the password
    and embeds a line break followed by ``FORGED_LOG_LINE``.
    """

    def __init__(
        self,
        password: str = 'fake-password',
        host: str = '127.0.0.1',
        port: int = 0,
        virtualserver_count: int = 2,
        flood_limit: int | None = None,
        flood_window: float = 3.0,
        hostile: bool = False,
    ):
        self._server = _Server((host, port), _Handler)
        self._server.password = password
        self._server.virtualservers = virtualservers(virtualserver_count)
        self._server.flood = _FloodGuard(flood_limit, flood_window)
        self._server.hostile = hostile
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    @property
    def virtualservers(self) -> list[dict[str, object]]:
        return self._server.virtualservers

    def start(self) -> FakeTs3Server:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def __enter__(self) -> FakeTs3Server:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument(
        '--password',
        default='fake-password',
        help='ServerQuery password the fake server accepts (see docs/testing.md)',
    )
    parser.add_argument('--port', type=int, default=0, help='0 picks a free port')
    parser.add_argument('--virtualservers', type=int, default=2)
    parser.add_argument('--flood-limit', type=int, default=None)
    args = parser.parse_args()

    server = FakeTs3Server(
        password=args.password,
        host=args.host,
        port=args.port,
        virtualserver_count=args.virtualservers,
        flood_limit=args.flood_limit,
    ).start()
    print(f'Fake TS3 ServerQuery listening on {server.host}:{server.port}')
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
