"""What a server can make one session cost: time, lines, bytes -- and
malformed trailers.
"""

from __future__ import annotations

import itertools

import pytest

from teamspeak_prometheus.errors import ServerQueryError
from teamspeak_prometheus.serverquery import (
    MAX_LINE_BYTES,
    MAX_SESSION_BYTES,
    MAX_SESSION_TIMEOUT_IN_SECONDS,
    PER_VIRTUALSERVER_TIMEOUT_IN_SECONDS,
    POLL_TIMEOUT_IN_SECONDS,
    SERVERQUERY_TIMEOUT_IN_SECONDS,
    ServerQueryClient,
)
from tests.client_support import (
    BANNER,
    FIXTURES,
    OK,
    app_client,
    client,
    flood_error,
    read_all,
    serverlist_reply,
    throttled_session,
    ticking_client,
)
from tests.fakes import FakeClock, FakeConnection


def test_a_trickling_server_hits_the_session_deadline():
    clock = FakeClock()
    connection = FakeConnection(
        BANNER, endless=itertools.repeat(b'x'), clock=clock, seconds_per_recv=1
    )
    query = ticking_client(connection, clock)

    with pytest.raises(TimeoutError, match='60s'):
        query.serverlist()

    assert connection.recv_calls <= 62  # banner, then about one byte a second


def test_endless_notifications_hit_the_session_deadline():
    clock = FakeClock()
    connection = FakeConnection(
        BANNER,
        endless=itertools.repeat(b'notifytextmessage msg=hi\n\r'),
        clock=clock,
        seconds_per_recv=1,
    )
    query = ticking_client(connection, clock)

    with pytest.raises(TimeoutError):
        query.serverlist()


def test_no_read_waits_past_the_session_deadline():
    clock = FakeClock()
    connection = FakeConnection(BANNER, OK, clock=clock, seconds_per_recv=0)
    query = ticking_client(connection, clock, timeout=4)
    connection.timeouts.clear()

    clock.now = 1.5
    query.use(1)

    assert connection.timeouts == [2.5]  # min(remaining, per-read timeout)


def test_every_read_is_bounded_by_the_per_read_timeout():
    clock = FakeClock()
    connection = FakeConnection(BANNER, OK, clock=clock)
    query = ticking_client(connection, clock, timeout=60)
    connection.timeouts.clear()

    query.use(1)

    assert connection.timeouts == [SERVERQUERY_TIMEOUT_IN_SECONDS]


def test_a_flood_wait_beyond_the_deadline_is_not_slept():
    clock = FakeClock()
    query = ticking_client(
        FakeConnection(BANNER, flood_error(10), clock=clock), clock, 5
    )

    with pytest.raises(TimeoutError):
        query.serverlist()

    assert clock.now == 0  # gave up instead of sleeping 10s


def test_an_overlong_line_is_rejected():
    chunk = b'x' * 65536
    connection = FakeConnection(BANNER, endless=itertools.repeat(chunk))
    query = ServerQueryClient(connection)

    with pytest.raises(ConnectionError, match='longer than'):
        query.serverlist()

    assert connection.recv_calls <= MAX_LINE_BYTES // len(chunk) + 3


def test_a_line_at_the_size_limit_is_accepted():
    payload = b'virtualserver_name=' + b'x' * (MAX_LINE_BYTES - 32)
    query, _ = client(payload + b'\n\r', OK)

    assert len(query.serverlist()[0]['virtualserver_name']) == MAX_LINE_BYTES - 32


@pytest.mark.parametrize(
    'trailer',
    [b'error msg=boom\n\r', b'error id= msg=boom\n\r', b'error id=abc msg=boom\n\r'],
    ids=['no-id', 'empty-id', 'non-numeric-id'],
)
def test_an_error_line_without_a_numeric_id_is_not_success(trailer: bytes):
    query, _ = client(b'virtualserver_id=1\n\r', trailer)

    with pytest.raises(ServerQueryError) as caught:
        query.serverlist()

    assert caught.value.error_id == -1
    assert 'malformed response' in str(caught.value)


def test_a_large_throttled_host_is_read_completely():
    # 150 virtualservers at 0.6s each: past the 60s base budget
    query, clock = throttled_session(150, seconds_per_recv=0.2)

    assert read_all(query) == 150
    assert clock.now > POLL_TIMEOUT_IN_SECONDS


def test_the_budget_grows_per_online_virtualserver():
    query, clock = throttled_session(3, seconds_per_recv=0)
    query.login('serveradmin', 'x')
    query.serverlist()

    clock.now = POLL_TIMEOUT_IN_SECONDS + 3 * PER_VIRTUALSERVER_TIMEOUT_IN_SECONDS - 1
    query.use(1)  # still inside the budget

    clock.now += 2
    with pytest.raises(TimeoutError, match='75s'):
        query.use(2)


def test_virtualservers_that_are_not_online_add_no_budget():
    query, clock = throttled_session(100, seconds_per_recv=0, status='offline')
    query.login('serveradmin', 'x')
    query.serverlist()

    clock.now = POLL_TIMEOUT_IN_SECONDS + 1
    with pytest.raises(TimeoutError):
        query.use(1)


def test_the_budget_is_capped_however_many_virtualservers_are_listed():
    # A hostile server listing thousands must not buy itself hours.
    query, clock = throttled_session(3_000, seconds_per_recv=0)
    query.login('serveradmin', 'x')
    query.serverlist()

    clock.now = MAX_SESSION_TIMEOUT_IN_SECONDS - 1
    query.use(1)

    clock.now = MAX_SESSION_TIMEOUT_IN_SECONDS + 1
    with pytest.raises(TimeoutError, match=f'{MAX_SESSION_TIMEOUT_IN_SECONDS:g}s'):
        query.use(2)


def test_a_trickling_server_is_still_cut_off_after_the_serverlist():
    clock = FakeClock()
    connection = FakeConnection(
        BANNER,
        OK,
        serverlist_reply(2),
        endless=itertools.repeat(b'x'),
        clock=clock,
        seconds_per_recv=1,
    )
    query = ServerQueryClient(connection, sleep=clock.sleep, clock=clock)
    query.login('serveradmin', 'x')
    query.serverlist()

    with pytest.raises(TimeoutError, match='70s'):
        query.use(1)


def test_a_session_that_sends_too_much_is_cut_off():
    # complete lines, each well under the line limit, without end
    line = b'notifytextmessage msg=' + b'x' * 60_000 + b'\n\r'
    connection = FakeConnection(BANNER, endless=itertools.repeat(line))
    query = app_client(connection)

    with pytest.raises(ConnectionError, match='more than'):
        query.serverlist()

    received = connection.recv_calls * len(line)
    assert received <= MAX_SESSION_BYTES + 2 * len(line)


def test_a_large_real_host_stays_far_inside_the_byte_budget():
    serverinfo = (FIXTURES / 'ts3-3.13.8-serverinfo.bin').read_bytes()
    count = 1_000
    replies = [BANNER, OK, serverlist_reply(count)] + [OK, serverinfo] * count
    query = app_client(FakeConnection(*replies))
    query.login('serveradmin', 'x')

    for server in query.serverlist():
        query.use(server['virtualserver_id'])
        query.serverinfo()

    assert count * len(serverinfo) < MAX_SESSION_BYTES / 10
