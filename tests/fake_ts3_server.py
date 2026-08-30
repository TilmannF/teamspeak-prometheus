"""A fake TeamSpeak 3 ServerQuery server.

It speaks enough of the real protocol -- banner, ``\\n\\r`` terminators,
escaping, ``error id=0 msg=ok`` trailers -- that both the archived ``ts3``
package and `tests.query_client` can drive it. It exists so this repository can
be developed and verified with no TeamSpeak server anywhere.
"""

from __future__ import annotations

import argparse
import socketserver
import threading

from tests.serverquery import (
    BANNER,
    LINE_TERMINATOR,
    decode_pairs,
    encode_pairs,
    encode_records,
)

VIRTUALSERVERS = [
    {'virtualserver_id': 1, 'virtualserver_name': 'Test Server'},
    {'virtualserver_id': 2, 'virtualserver_name': 'Zweiter Server'},
]


# Every metric this exporter reads, plus the name it labels them with. Values
# are arbitrary but distinct per virtualserver so tests can tell them apart.
def serverinfo(virtualserver_id: int) -> dict[str, object]:
    from app import METRICS_NAMES

    name = next(
        server['virtualserver_name']
        for server in VIRTUALSERVERS
        if server['virtualserver_id'] == virtualserver_id
    )
    info: dict[str, object] = {
        'virtualserver_id': virtualserver_id,
        'virtualserver_name': name,
    }
    for offset, metric in enumerate(METRICS_NAMES):
        info[metric] = virtualserver_id * 1000 + offset
    return info


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        self.wfile.write(BANNER)
        selected = 1
        while True:
            line = self.rfile.readline()
            if not line:
                return
            command, _, arguments = line.decode('utf-8').strip().partition(' ')
            keys = decode_pairs(arguments)

            if command == 'quit':
                self._ok()
                return
            if command == 'login':
                if keys.get('client_login_password') == self.server.password:
                    self._ok()
                else:
                    self._error(520, 'invalid loginname or password')
                continue
            if command == 'serverlist':
                self._data(encode_records(VIRTUALSERVERS))
                continue
            if command == 'use':
                selected = int(keys.get('sid') or 1)
                self._ok()
                continue
            if command == 'serverinfo':
                self._data(encode_pairs(serverinfo(selected)))
                continue
            self._error(256, 'command not found')

    def _write(self, payload: str) -> None:
        self.wfile.write(payload.encode('utf-8') + LINE_TERMINATOR)

    def _ok(self) -> None:
        self._write('error id=0 msg=ok')

    def _error(self, code: int, message: str) -> None:
        self._write('error id=%d msg=%s' % (code, message.replace(' ', '\\s')))

    def _data(self, payload: str) -> None:
        self._write(payload)
        self._ok()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    password = ''


class FakeTs3Server:
    """Runs the fake ServerQuery interface on an ephemeral port."""

    def __init__(
        self,
        password: str = 'fake-password',
        host: str = '127.0.0.1',
        port: int = 0,
    ):
        self._server = _Server((host, port), _Handler)
        self._server.password = password
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def host(self) -> str:
        return self._server.server_address[0]

    @property
    def port(self) -> int:
        return self._server.server_address[1]

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
        help='ServerQuery password the fake server accepts (default: %(default)s)',
    )
    parser.add_argument('--port', type=int, default=0, help='0 picks a free port')
    args = parser.parse_args()

    server = FakeTs3Server(
        password=args.password, host=args.host, port=args.port
    ).start()
    print('Fake TS3 ServerQuery listening on %s:%d' % (server.host, server.port))
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
