"""In-process fakes for unit tests. No sockets, no ``ts3`` package."""

from __future__ import annotations

from dataclasses import dataclass, field

from app import METRICS_NAMES


def serverinfo(virtualserver_name: str, base: int = 0) -> dict[str, object]:
    """A complete ``serverinfo`` payload with distinct values per metric."""

    info: dict[str, object] = {'virtualserver_name': virtualserver_name}
    for offset, metric in enumerate(METRICS_NAMES):
        info[metric] = base + offset
    return info


@dataclass
class FakeResponse:
    data: list[dict[str, object]]
    msg: str = 'ok'

    @property
    def response(self) -> dict[str, str]:
        return {'id': '0' if self.msg == 'ok' else '1', 'msg': self.msg}


@dataclass
class FakeTs3Client:
    """Implements ``app.Ts3Client`` with canned responses and a call log."""

    servers: list[dict[str, object]] = field(
        default_factory=lambda: [
            {'virtualserver_id': 1, 'virtualserver_name': 'First'},
            {'virtualserver_id': 2, 'virtualserver_name': 'Second'},
        ]
    )
    login_succeeds: bool = True
    serverlist_msg: str = 'ok'
    serverinfo_msg: str = 'ok'
    calls: list[tuple[str, object]] = field(default_factory=list)
    disconnected: bool = False

    def login(self, username: str, password: str) -> bool:
        self.calls.append(('login', username))
        return self.login_succeeds

    def serverlist(self) -> FakeResponse:
        self.calls.append(('serverlist', None))
        return FakeResponse(data=list(self.servers), msg=self.serverlist_msg)

    def use(self, virtualserver_id: object) -> FakeResponse:
        self.calls.append(('use', virtualserver_id))
        self._selected = virtualserver_id
        return FakeResponse(data=[])

    def send_command(self, command: str) -> FakeResponse:
        self.calls.append(('send_command', command))
        selected = getattr(self, '_selected', 1)
        name = next(
            server['virtualserver_name']
            for server in self.servers
            if server['virtualserver_id'] == selected
        )
        base = 1000 * int(selected)
        return FakeResponse(data=[serverinfo(str(name), base)], msg=self.serverinfo_msg)

    def disconnect(self) -> None:
        self.calls.append(('disconnect', None))
        self.disconnected = True


def factory_for(client: FakeTs3Client):
    """A ``ClientFactory`` that always hands back ``client``."""

    def make(host: str, port: int) -> FakeTs3Client:
        client.calls.append(('connect', (host, port)))
        return client

    return make
