"""In-process fakes for unit tests. No sockets."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from teamspeak_prometheus.errors import LoginFailed, ServerQueryError
from teamspeak_prometheus.metrics import METRICS_NAMES


def serverinfo(virtualserver_name: str, base: int = 0) -> dict[str, object]:
    """A complete ``serverinfo`` payload with distinct values per metric."""

    info: dict[str, object] = {'virtualserver_name': virtualserver_name}
    for offset, metric in enumerate(METRICS_NAMES):
        info[metric] = base + offset
    return info


@dataclass
class FakeTs3Client:
    """Implements ``serverquery.Ts3Client`` with canned responses and a call log."""

    servers: list[dict[str, object]] = field(
        default_factory=lambda: [
            {'virtualserver_id': 1, 'virtualserver_name': 'First'},
            {'virtualserver_id': 2, 'virtualserver_name': 'Second'},
        ]
    )
    login_succeeds: bool = True
    # a ServerQueryError the login raises instead, e.g. flooding
    login_error: ServerQueryError | None = None
    serverlist_error: str | None = None
    # virtualserver ids whose ``use`` fails, e.g. because they are stopped
    offline: set[int] = field(default_factory=set)
    offline_message: str = 'server is not running'
    # field names left out of every serverinfo payload
    missing: set[str] = field(default_factory=set)
    calls: list[tuple[str, object]] = field(default_factory=list)
    closed: bool = False
    _selected: object = None

    def login(self, username: str, password: str) -> None:
        self.calls.append(('login', username))
        if self.login_error is not None:
            raise self.login_error
        if not self.login_succeeds:
            raise LoginFailed('login', 520, 'invalid loginname or password')

    def serverlist(self) -> list[dict[str, object]]:
        self.calls.append(('serverlist', None))
        if self.serverlist_error:
            raise ServerQueryError('serverlist', 1281, self.serverlist_error)
        return list(self.servers)

    def use(self, virtualserver_id: object) -> None:
        self.calls.append(('use', virtualserver_id))
        if virtualserver_id in self.offline:
            raise ServerQueryError('use', 1033, self.offline_message)
        self._selected = virtualserver_id

    def serverinfo(self) -> dict[str, object]:
        self.calls.append(('serverinfo', self._selected))
        name = next(
            server['virtualserver_name']
            for server in self.servers
            if server['virtualserver_id'] == self._selected
        )
        info = serverinfo(str(name), 1000 * int(self._selected))
        return {key: value for key, value in info.items() if key not in self.missing}

    def close(self) -> None:
        self.calls.append(('close', None))
        self.closed = True


def factory_for(client: FakeTs3Client):
    """A ``ClientFactory`` that always hands back ``client``."""

    def make(host: str, port: int) -> FakeTs3Client:
        client.calls.append(('connect', (host, port)))
        return client

    return make


@dataclass
class FakeClock:
    """A monotonic clock tests advance by hand."""

    now: float = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def unreachable(host: str, port: int) -> FakeTs3Client:
    """A ``ClientFactory`` for a server that refuses connections."""

    raise ConnectionRefusedError(111, 'Connection refused')


class FakeConnection:
    """A scripted socket: ``recv`` hands out ``replies`` one chunk at a time.

    Everything the client sends is kept in ``sent`` so tests can assert on the
    exact wire bytes. ``replies`` may be an endless iterator; ``clock`` then
    lets each ``recv`` advance a fake clock by ``seconds_per_recv``.
    """

    def __init__(
        self,
        *replies: bytes,
        endless: Iterator[bytes] | None = None,
        clock: FakeClock | None = None,
        seconds_per_recv: float = 0.0,
    ) -> None:
        self.replies = list(replies)
        self.endless = endless
        self.clock = clock
        self.seconds_per_recv = seconds_per_recv
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.recv_calls = 0
        self.closed = False

    def sendall(self, data: bytes, /) -> None:
        self.sent.append(data)

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def recv(self, bufsize: int, /) -> bytes:
        self.recv_calls += 1
        if self.clock is not None:
            self.clock.now += self.seconds_per_recv
        if self.replies:
            return self.replies.pop(0)
        if self.endless is not None:
            return next(self.endless)
        return b''

    def close(self) -> None:
        self.closed = True
