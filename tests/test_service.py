"""The polling service: login, the serverlist/use/serverinfo sequence, errors."""

from __future__ import annotations

import pytest

import app
from tests.fakes import FakeTs3Client, factory_for


def service(config_overrides=None, **client_kwargs):
    client = FakeTs3Client(**client_kwargs)
    config = app.Config(
        host='ts.example.com',
        port=10011,
        username='serveradmin',
        password='secret',
        metrics_port=8000,
        **(config_overrides or {}),
    )
    registry_gauges = app.build_gauges(_fresh_registry())
    return app.Teamspeak3MetricService(
        config, registry_gauges, factory_for(client)
    ), client


def _fresh_registry():
    from prometheus_client import CollectorRegistry

    return CollectorRegistry()


def test_connect_uses_the_configured_host_and_port():
    metric_service, client = service()

    metric_service.connect()

    assert ('connect', ('ts.example.com', 10011)) in client.calls
    assert ('login', 'serveradmin') in client.calls


def test_a_rejected_login_raises_login_failed():
    metric_service, _ = service(login_succeeds=False)

    with pytest.raises(app.LoginFailed):
        metric_service.connect()


def test_read_before_connect_is_an_explicit_error():
    metric_service, _ = service()

    with pytest.raises(app.ExporterError, match='connect'):
        metric_service.read()


def test_every_virtualserver_is_selected_and_read():
    metric_service, client = service()
    metric_service.connect()

    metric_service.read()

    assert [call for call in client.calls if call[0] == 'use'] == [
        ('use', 1),
        ('use', 2),
    ]
    assert [call for call in client.calls if call[0] == 'send_command'] == [
        ('send_command', 'serverinfo'),
        ('send_command', 'serverinfo'),
    ]


def test_metrics_are_recorded_per_virtualserver():
    metric_service, _ = service()
    metric_service.connect()

    metric_service.read()

    first = metric_service.gauges['virtualserver_clientsonline'].labels(
        virtualserver_name='First'
    )
    second = metric_service.gauges['virtualserver_clientsonline'].labels(
        virtualserver_name='Second'
    )
    assert first._value.get() != second._value.get()


def test_a_failed_serverlist_skips_the_cycle_without_raising(capsys):
    metric_service, client = service(serverlist_msg='database empty result set')
    metric_service.connect()

    metric_service.read()

    assert 'Error retrieving serverlist' in capsys.readouterr().out
    assert not [call for call in client.calls if call[0] == 'use']


def test_a_failed_serverinfo_stops_the_cycle_without_raising(capsys):
    metric_service, client = service(serverinfo_msg='invalid serverID')
    metric_service.connect()

    metric_service.read()

    assert 'Error retrieving serverinfo' in capsys.readouterr().out
    assert len([call for call in client.calls if call[0] == 'send_command']) == 1


def test_disconnect_releases_the_client():
    metric_service, client = service()
    metric_service.connect()

    metric_service.disconnect()

    assert client.disconnected
    assert metric_service.client is None


def test_disconnect_without_a_connection_is_harmless():
    metric_service, client = service()

    metric_service.disconnect()

    assert not client.disconnected


def test_poll_forever_runs_the_full_cycle_per_iteration():
    metric_service, client = service()

    app.poll_forever(metric_service, interval_in_seconds=0, iterations=2)

    assert [call[0] for call in client.calls].count('login') == 2
    assert [call[0] for call in client.calls].count('disconnect') == 2
