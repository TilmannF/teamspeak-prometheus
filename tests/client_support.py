"""Shared helpers of the ServerQuery client tests: scripted replies."""

from __future__ import annotations

from pathlib import Path

from teamspeak_prometheus.serverquery import (
    ServerQueryClient,
)
from tests.fakes import FakeClock, FakeConnection

FIXTURES = Path(__file__).parent / 'fixtures'


BANNER = b'TS3\n\rWelcome to the TeamSpeak 3 ServerQuery interface.\n\r'


OK = b'error id=0 msg=ok\n\r'


def flood_error(seconds: int) -> bytes:
    return (
        b'error id=524 msg=client\\sis\\sflooding '
        b'extra_msg=please\\swait\\s%d\\sseconds\n\r' % seconds
    )


def client(*replies: bytes, sleeps: list[float] | None = None):
    connection = FakeConnection(BANNER, *replies)
    record = sleeps if sleeps is not None else []
    return ServerQueryClient(connection, sleep=record.append), connection


def ticking_client(connection: FakeConnection, clock: FakeClock, timeout: float = 60):
    return ServerQueryClient(
        connection, sleep=clock.sleep, clock=clock, timeout=timeout
    )


def serverlist_reply(count: int, status: str = 'online') -> bytes:
    records = b'|'.join(
        b'virtualserver_id=%d virtualserver_status=%s' % (sid, status.encode())
        for sid in range(1, count + 1)
    )
    return records + b'\n\r' + OK


def throttled_session(count: int, seconds_per_recv: float, status: str = 'online'):
    """A session that lists ``count`` virtualservers, each pair of use and
    serverinfo taking ``3 * seconds_per_recv`` -- TeamSpeak's default flood
    pace is about 0.6s per pair for a client not on the allowlist."""

    clock = FakeClock()
    replies = [BANNER, OK, serverlist_reply(count, status)]
    replies += [OK, b'virtualserver_name=x\n\r', OK] * count
    connection = FakeConnection(
        *replies, clock=clock, seconds_per_recv=seconds_per_recv
    )
    return ServerQueryClient(connection, sleep=clock.sleep, clock=clock), clock


def read_all(query: ServerQueryClient) -> int:
    query.login('serveradmin', 'x')
    read = 0
    for server in query.serverlist():
        query.use(server['virtualserver_id'])
        query.serverinfo()
        read += 1
    return read


def app_client(connection: FakeConnection) -> ServerQueryClient:
    return ServerQueryClient(connection)
