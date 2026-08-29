"""Proves the fake ServerQuery server also satisfies the real ``ts3`` package.

The archived ``ts3`` package depends on ``telnetlib``, removed in Python 3.13,
so this test only runs on 3.12 and below. Everything else in the suite is
deliberately independent of it -- see docs/modernization-backlog.md.
"""

from __future__ import annotations

import pytest

from tests.fake_ts3_server import FakeTs3Server

ts3 = pytest.importorskip('ts3', reason='ts3 requires telnetlib (Python <= 3.12)')

pytestmark = pytest.mark.smoke

PASSWORD = 'wire-test-password'


@pytest.fixture
def fake_server():
    with FakeTs3Server(password=PASSWORD) as server:
        yield server


def test_the_real_client_can_read_serverinfo(fake_server):
    import app

    client = ts3.TS3Server(fake_server.host, fake_server.port)
    assert client.login('serveradmin', PASSWORD) is True

    serverlist = client.serverlist()
    assert serverlist.response['msg'] == 'ok'
    assert len(serverlist.data) == 2

    client.use(serverlist.data[0]['virtualserver_id'])
    serverinfo = client.send_command('serverinfo')
    assert serverinfo.response['msg'] == 'ok'

    payload = serverinfo.data[0]
    assert payload['virtualserver_name'] == 'Test Server'
    for metric in app.METRICS_NAMES:
        assert metric in payload

    client.disconnect()


def test_a_wrong_password_is_rejected(fake_server):
    client = ts3.TS3Server(fake_server.host, fake_server.port)

    assert client.login('serveradmin', 'wrong') is False

    client.disconnect()
