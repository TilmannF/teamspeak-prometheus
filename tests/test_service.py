"""The polling service: poll sequence, error survival, self-metrics, series."""

from __future__ import annotations

import signal

import pytest
from prometheus_client import CollectorRegistry

import app
from tests.fakes import FakeTs3Client, factory_for, unreachable

CONFIG = app.Config(
    host='ts.example.com',
    port=10011,
    username='serveradmin',
    password='secret',
    metrics_port=8000,
)


class Setup:
    def __init__(self, factory=None, **client_kwargs):
        self.registry = CollectorRegistry()
        self.client = FakeTs3Client(**client_kwargs)
        self.now = 1_700_000_000.0
        self.service = app.Teamspeak3MetricService(
            CONFIG,
            app.build_gauges(self.registry),
            app.build_exporter_metrics(self.registry),
            factory or factory_for(self.client),
            clock=lambda: self.now,
        )

    def value(self, name: str, **labels: str) -> float | None:
        return self.registry.get_sample_value(name, labels)

    def calls(self, kind: str) -> list[tuple[str, object]]:
        return [call for call in self.client.calls if call[0] == kind]


def test_a_poll_connects_logs_in_and_reads_every_virtualserver():
    setup = Setup()

    assert setup.service.poll() is app.PollResult.OK

    assert ('connect', ('ts.example.com', 10011)) in setup.client.calls
    assert setup.calls('login') == [('login', 'serveradmin')]
    assert setup.calls('use') == [('use', 1), ('use', 2)]
    assert setup.client.closed


def test_metrics_are_recorded_per_virtualserver():
    setup = Setup()

    setup.service.poll()

    first = setup.value(
        'teamspeak_virtualserver_clientsonline', virtualserver_name='First'
    )
    second = setup.value(
        'teamspeak_virtualserver_clientsonline', virtualserver_name='Second'
    )
    assert first is not None and second is not None and first != second


def test_a_successful_poll_sets_the_self_metrics():
    setup = Setup()

    setup.service.poll()

    assert setup.value('teamspeak_exporter_poll_success') == 1
    assert setup.value('teamspeak_exporter_last_poll_timestamp_seconds') == setup.now
    assert (
        setup.value('teamspeak_exporter_last_successful_poll_timestamp_seconds')
        == setup.now
    )
    assert (
        setup.value('teamspeak_exporter_missing_fields', virtualserver_name='First')
        == 0
    )


def test_an_unreachable_server_is_counted_not_raised():
    setup = Setup(factory=unreachable)

    assert setup.service.poll() is app.PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_success') == 0
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='connection') == 1
    assert setup.value('teamspeak_exporter_last_poll_timestamp_seconds') == setup.now
    assert setup.value('teamspeak_exporter_last_successful_poll_timestamp_seconds') == 0


def test_a_rejected_login_is_counted_and_closes_the_connection():
    setup = Setup(login_succeeds=False)

    assert setup.service.poll() is app.PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='login') == 1
    assert setup.client.closed


def test_a_login_error_other_than_rejection_counts_as_a_query_error():
    setup = Setup(login_error=app.ServerQueryError('login', 524, 'client is flooding'))

    assert setup.service.poll() is app.PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 1
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='login') == 0


def test_a_failed_serverlist_fails_the_poll():
    setup = Setup(serverlist_error='database empty result set')

    assert setup.service.poll() is app.PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 1
    assert setup.calls('use') == []


def test_an_offline_virtualserver_is_skipped_and_the_rest_still_read():
    setup = Setup(offline={1})

    assert setup.service.poll() is app.PollResult.PARTIAL

    assert setup.calls('serverinfo') == [('serverinfo', 2)]
    assert (
        setup.value('teamspeak_virtualserver_uptime', virtualserver_name='Second')
        is not None
    )
    assert setup.value('teamspeak_exporter_poll_success') == 0
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 1


def test_a_session_deadline_counts_as_a_connection_error():
    def hanging(host: str, port: int):
        raise TimeoutError('ServerQuery session did not finish within 60s')

    setup = Setup(factory=hanging)

    assert setup.service.poll() is app.PollResult.FAILED
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='connection') == 1


def stopped_second(status: str = 'offline') -> list[dict[str, object]]:
    return [
        {
            'virtualserver_id': 1,
            'virtualserver_name': 'First',
            'virtualserver_status': 'online',
        },
        {
            'virtualserver_id': 2,
            'virtualserver_name': 'Second',
            'virtualserver_status': status,
        },
    ]


@pytest.mark.parametrize(
    'status', ['offline', 'virtual', 'booting up', 'deploy running']
)
def test_a_virtualserver_that_is_not_online_is_skipped_without_error(status):
    setup = Setup(servers=stopped_second(status))

    assert setup.service.poll() is app.PollResult.OK

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


def test_an_unexpected_exception_does_not_escape_the_poll():
    def broken(host: str, port: int):
        raise RuntimeError('boom')

    setup = Setup(factory=broken)

    assert setup.service.poll() is app.PollResult.FAILED
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='unexpected') == 1


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


def test_a_shutdown_is_not_swallowed_by_the_poll():
    def terminated(host: str, port: int):
        raise app.Shutdown

    setup = Setup(factory=terminated)

    with pytest.raises(app.Shutdown):
        setup.service.poll()


def test_a_shutdown_mid_session_still_closes_the_connection():
    setup = Setup()

    def interrupted() -> list[dict[str, object]]:
        raise app.Shutdown

    setup.client.serverlist = interrupted

    with pytest.raises(app.Shutdown):
        setup.service.poll()

    assert setup.client.closed


def test_sigterm_raises_shutdown():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        app.handle_termination()
        handler = signal.getsignal(signal.SIGTERM)

        with pytest.raises(app.Shutdown):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_the_password_never_reaches_the_log(caplog):
    setup = Setup(login_succeeds=False)

    setup.service.poll()

    assert caplog.records
    assert CONFIG.password not in caplog.text


class Loop:
    """A fake clock and sleep for ``poll_forever``."""

    def __init__(self, poll_seconds: float = 0.0):
        self.now = 0.0
        self.sleeps: list[float] = []
        self.poll_seconds = poll_seconds

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedService:
    def __init__(self, loop: Loop, results: list[app.PollResult]):
        self.loop = loop
        self.results = list(results)

    def poll(self) -> app.PollResult:
        self.loop.now += self.loop.poll_seconds
        return self.results.pop(0)


def test_the_interval_is_measured_start_to_start():
    loop = Loop(poll_seconds=1.5)
    service = ScriptedService(loop, [app.PollResult.OK] * 3)

    app.poll_forever(service, 5, iterations=3, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [3.5, 3.5]


def test_failed_polls_back_off_and_success_resets():
    loop = Loop()
    failed, ok = app.PollResult.FAILED, app.PollResult.OK
    service = ScriptedService(loop, [failed, failed, failed, ok, ok])

    app.poll_forever(service, 5, iterations=5, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [10, 20, 40, 5]


def test_a_partial_poll_does_not_back_off():
    loop = Loop()
    service = ScriptedService(loop, [app.PollResult.PARTIAL] * 2)

    app.poll_forever(service, 5, iterations=2, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [5]


@pytest.mark.parametrize(
    ('interval', 'failures', 'expected'),
    [(5, 0, 5), (5, 1, 10), (5, 4, 60), (5, 50, 60), (120, 3, 120)],
)
def test_backoff_is_capped_but_never_below_the_interval(interval, failures, expected):
    assert app.next_delay(interval, failures) == expected
