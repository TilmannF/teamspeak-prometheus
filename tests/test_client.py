"""The ServerQuery client, driven over a scripted in-memory connection."""

from __future__ import annotations

from pathlib import Path

import pytest

import app
from tests.fakes import FakeConnection
from tests.serverquery import escape as reference_escape

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
    return app.ServerQueryClient(connection, sleep=record.append), connection


def test_the_real_ts3_banner_is_accepted():
    connection = FakeConnection((FIXTURES / 'ts3-3.13.8-banner.bin').read_bytes(), OK)
    query = app.ServerQueryClient(connection)

    query.use(1)

    assert connection.sent == [b'use sid=1\n\r']


def test_a_server_that_is_not_serverquery_is_rejected():
    with pytest.raises(ConnectionError, match='not a TeamSpeak 3'):
        app.ServerQueryClient(FakeConnection(b'SSH-2.0-OpenSSH\n\r'))


def test_a_silent_close_before_the_greeting_hints_at_a_ban():
    with pytest.raises(ConnectionError, match='banned'):
        app.ServerQueryClient(FakeConnection())


def test_login_sends_escaped_credentials():
    query, connection = client(OK)

    query.login('serveradmin', 'pass word|x')

    assert connection.sent == [
        b'login client_login_name=serveradmin client_login_password=pass\\sword\\px\n\r'
    ]


def test_a_rejected_login_raises_login_failed_without_the_password():
    query, _ = client(b'error id=520 msg=invalid\\sloginname\\sor\\spassword\n\r')

    with pytest.raises(app.LoginFailed) as caught:
        query.login('serveradmin', 'hunter2')

    assert caught.value.error_id == 520
    assert 'hunter2' not in str(caught.value)


def test_a_login_still_flooded_after_all_retries_is_not_a_rejected_login():
    flood = flood_error(1)
    query, _ = client(*[flood] * (app.FLOOD_RETRIES + 1))

    with pytest.raises(app.ServerQueryError) as caught:
        query.login('serveradmin', 'hunter2')

    assert not isinstance(caught.value, app.LoginFailed)
    assert caught.value.error_id == 524


@pytest.mark.parametrize(
    'error',
    [
        b'error id=3329 msg=connection\\sfailed,\\syou\\sare\\sbanned\n\r',
        b'error id=2568 msg=insufficient\\sclient\\spermissions\n\r',
    ],
)
def test_other_login_errors_are_not_rejected_logins(error: bytes):
    query, _ = client(error)

    with pytest.raises(app.ServerQueryError) as caught:
        query.login('serveradmin', 'hunter2')

    assert not isinstance(caught.value, app.LoginFailed)


def test_the_real_serverlist_response_is_parsed():
    query, _ = client((FIXTURES / 'ts3-3.13.8-serverlist.bin').read_bytes())

    servers = query.serverlist()

    assert servers[0]['virtualserver_id'] == '1'
    assert servers[0]['virtualserver_name'] == 'TeamSpeak ]I[ Server'
    assert servers[0]['virtualserver_machine_id'] is None


def test_the_real_serverinfo_response_contains_every_contract_field():
    query, _ = client((FIXTURES / 'ts3-3.13.8-serverinfo.bin').read_bytes())

    info = query.serverinfo()

    missing = [name for name in app.METRICS_NAMES if name not in info]
    assert missing == []
    for name in app.METRICS_NAMES:
        float(info[name])


def test_multiple_records_are_split_on_pipes():
    query, _ = client(b'virtualserver_id=1|virtualserver_id=2\n\r', OK)

    assert [s['virtualserver_id'] for s in query.serverlist()] == ['1', '2']


def test_a_response_split_across_reads_is_reassembled():
    query, _ = client(
        b'virtualserver_id=1 virtualserver_na', b'me=A\n\rerror id=0 ', b'msg=ok\n\r'
    )

    assert query.serverlist() == [{'virtualserver_id': '1', 'virtualserver_name': 'A'}]


def test_notifications_between_commands_are_ignored():
    query, _ = client(
        b'notifycliententerview clid=5\n\r', b'virtualserver_id=1\n\r', OK
    )

    assert query.serverlist() == [{'virtualserver_id': '1'}]


def test_an_error_response_raises_with_its_id_and_message():
    query, _ = client(b'error id=1024 msg=invalid\\sserverID\n\r')

    with pytest.raises(app.ServerQueryError) as caught:
        query.use(99)

    assert caught.value.error_id == 1024
    assert caught.value.message == 'invalid serverID'


def test_flooding_waits_as_told_and_retries():
    sleeps: list[float] = []
    query, connection = client(
        flood_error(2),
        b'virtualserver_id=1\n\r',
        OK,
        sleeps=sleeps,
    )

    assert query.serverlist() == [{'virtualserver_id': '1'}]
    assert sleeps == [2.0]
    assert connection.sent == [b'serverlist\n\r', b'serverlist\n\r']


def test_flooding_gives_up_after_the_retry_budget():
    flood = flood_error(1)
    sleeps: list[float] = []
    query, _ = client(*[flood] * (app.FLOOD_RETRIES + 1), sleeps=sleeps)

    with pytest.raises(app.ServerQueryError) as caught:
        query.serverlist()

    assert caught.value.error_id == 524
    assert len(sleeps) == app.FLOOD_RETRIES


def test_an_absurd_flood_wait_is_capped():
    sleeps: list[float] = []
    query, _ = client(
        b'error id=524 msg=flooding extra_msg=please\\swait\\s600\\sseconds\n\r',
        OK,
        sleeps=sleeps,
    )

    query.use(1)

    assert sleeps == [app.MAX_FLOOD_WAIT_IN_SECONDS]


def test_a_closed_connection_raises_connection_error():
    query, _ = client()

    with pytest.raises(ConnectionError):
        query.serverlist()


def test_close_says_quit_and_closes_the_socket():
    query, connection = client()

    query.close()

    assert connection.sent == [b'quit\n\r']
    assert connection.closed


@pytest.mark.parametrize(
    'value',
    [
        'plain',
        'Test Server',
        'pipe|x',
        'slash/x',
        'back\\slash',
        'looks\\slike',
        'Ümläute',
    ],
)
def test_escaping_matches_the_reference_encoder(value: str):
    assert app.escape(value) == reference_escape(value)
    assert app.unescape(reference_escape(value)) == value
