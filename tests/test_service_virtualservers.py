"""Virtualservers as a host has them: stopped, renamed, removed,
sharing a name, or missing fields.
"""

from __future__ import annotations

import pytest

from teamspeak_prometheus.metrics import (
    METRICS_NAMES,
)
from teamspeak_prometheus.service import PollResult
from tests.fakes import unreachable
from tests.service_support import Setup, same_name, stopped_second


@pytest.mark.parametrize(
    'status', ['offline', 'virtual', 'booting up', 'deploy running']
)
def test_a_virtualserver_that_is_not_online_is_skipped_without_error(status):
    setup = Setup(servers=stopped_second(status))

    assert setup.service.poll() is PollResult.OK

    assert setup.calls('use') == [('use', 1)]
    assert setup.value('teamspeak_exporter_poll_success') == 1
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 0
    assert (
        setup.value('teamspeak_exporter_last_successful_poll_timestamp_seconds')
        == setup.now
    )


def test_a_virtualserver_that_stops_loses_its_series():
    setup = Setup()
    setup.service.poll()
    setup.client.servers = stopped_second()

    setup.service.poll()

    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='Second')
        is None
    )
    assert (
        setup.value('teamspeak_exporter_missing_fields', virtualserver_name='Second')
        is None
    )
    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='First')
        is not None
    )


def test_a_stopped_virtualserver_is_logged_once_and_again_when_back(caplog):
    setup = Setup(servers=stopped_second())
    caplog.set_level('INFO', logger='teamspeak_prometheus')

    setup.service.poll()
    setup.service.poll()
    setup.client.servers = stopped_second('online')
    setup.service.poll()

    messages = [r.getMessage() for r in caplog.records]
    assert sum("2 'Second' is offline" in m for m in messages) == 1
    assert sum("2 'Second' is online again" in m for m in messages) == 1
    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='Second')
        is not None
    )


def test_a_virtualserver_without_a_status_is_read():
    # serverlist always reports a status on TeamSpeak 3.13; be lenient anyway
    setup = Setup()

    setup.service.poll()

    assert setup.calls('use') == [('use', 1), ('use', 2)]


def test_virtualservers_sharing_a_name_are_warned_about_once(caplog):
    setup = Setup(servers=same_name(3))

    setup.service.poll()
    setup.service.poll()

    warnings = [
        r.getMessage() for r in caplog.records if 'are all named' in r.getMessage()
    ]
    assert warnings == [
        "Virtualservers 1, 2, 3 are all named 'TeamSpeak ]I[ Server'; they share "
        'one set of series and only the last one read is exported. Give them '
        'distinct names.'
    ]


def test_the_last_virtualserver_read_wins_a_shared_name():
    setup = Setup(servers=same_name())

    assert setup.service.poll() is PollResult.OK

    series = [
        sample
        for metric in setup.registry.collect()
        if metric.name == 'teamspeak_virtualserver_uptime'
        for sample in metric.samples
    ]
    assert len(series) == 1
    assert series[0].value == 2000 + METRICS_NAMES.index('virtualserver_uptime')


def test_a_duplicate_name_that_returns_is_warned_about_again(caplog):
    setup = Setup(servers=same_name())
    setup.service.poll()
    setup.client.servers = [
        {'virtualserver_id': 1, 'virtualserver_name': 'TeamSpeak ]I[ Server'},
        {'virtualserver_id': 2, 'virtualserver_name': 'Renamed'},
    ]
    setup.service.poll()
    setup.client.servers = same_name()

    setup.service.poll()

    assert sum('are all named' in r.getMessage() for r in caplog.records) == 2


def test_distinct_names_are_not_warned_about(caplog):
    Setup().service.poll()

    assert not any('are all named' in r.getMessage() for r in caplog.records)


def test_missing_fields_are_skipped_counted_and_logged_once(caplog):
    setup = Setup(missing={'virtualserver_uptime', 'virtualserver_total_ping'})

    setup.service.poll()
    setup.service.poll()

    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='First')
        is None
    )
    assert (
        setup.value('teamspeak_virtualserver_clientsonline', virtualserver_name='First')
        is not None
    )
    assert (
        setup.value('teamspeak_exporter_missing_fields', virtualserver_name='First')
        == 2
    )
    warnings = [r for r in caplog.records if 'virtualserver_uptime' in r.getMessage()]
    assert len(warnings) == 2  # once per virtualserver, not once per poll


def test_a_field_that_disappears_loses_its_stale_value():
    setup = Setup()
    setup.service.poll()
    setup.client.missing = {'virtualserver_uptime'}

    setup.service.poll()

    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='First')
        is None
    )


def test_a_removed_virtualserver_loses_its_series():
    setup = Setup()
    setup.service.poll()
    setup.client.servers = [
        s for s in setup.client.servers if s['virtualserver_id'] == 1
    ]

    setup.service.poll()

    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='First')
        is not None
    )
    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='Second')
        is None
    )
    assert (
        setup.value('teamspeak_exporter_missing_fields', virtualserver_name='Second')
        is None
    )


def test_series_survive_a_failed_poll():
    setup = Setup()
    setup.service.poll()
    setup.service.client_factory = unreachable

    setup.service.poll()

    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='First')
        is not None
    )
