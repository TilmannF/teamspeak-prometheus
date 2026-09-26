"""The polling service: one poll, its errors, and what it censors."""

from __future__ import annotations

import signal
import time

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from teamspeak_prometheus.errors import ServerQueryError
from teamspeak_prometheus.main import Shutdown, handle_termination
from teamspeak_prometheus.metrics import (
    build_exporter_metrics,
    build_gauges,
)
from teamspeak_prometheus.service import PollResult, Teamspeak3MetricService
from tests.fakes import FakeTs3Client, factory_for, unreachable
from tests.service_support import CONFIG, Setup


def test_a_poll_connects_logs_in_and_reads_every_virtualserver():
    setup = Setup()

    assert setup.service.poll() is PollResult.OK

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

    assert setup.service.poll() is PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_success') == 0
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='connection') == 1
    assert setup.value('teamspeak_exporter_last_poll_timestamp_seconds') == setup.now
    assert setup.value('teamspeak_exporter_last_successful_poll_timestamp_seconds') == 0


def test_a_rejected_login_is_counted_and_closes_the_connection():
    setup = Setup(login_succeeds=False)

    assert setup.service.poll() is PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='login') == 1
    assert setup.client.closed


def test_a_login_error_other_than_rejection_counts_as_a_query_error():
    setup = Setup(login_error=ServerQueryError('login', 524, 'client is flooding'))

    assert setup.service.poll() is PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 1
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='login') == 0


def test_a_failed_serverlist_fails_the_poll():
    setup = Setup(serverlist_error='database empty result set')

    assert setup.service.poll() is PollResult.FAILED

    assert setup.value('teamspeak_exporter_poll_errors_total', reason='query') == 1
    assert setup.calls('use') == []


def test_an_offline_virtualserver_is_skipped_and_the_rest_still_read():
    setup = Setup(offline={1})

    assert setup.service.poll() is PollResult.PARTIAL

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

    assert setup.service.poll() is PollResult.FAILED
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='connection') == 1


def test_an_unexpected_exception_does_not_escape_the_poll():
    def broken(host: str, port: int):
        raise RuntimeError('boom')

    setup = Setup(factory=broken)

    assert setup.service.poll() is PollResult.FAILED
    assert setup.value('teamspeak_exporter_poll_errors_total', reason='unexpected') == 1


def test_a_shutdown_is_not_swallowed_by_the_poll():
    def terminated(host: str, port: int):
        raise Shutdown

    setup = Setup(factory=terminated)

    with pytest.raises(Shutdown):
        setup.service.poll()


def test_a_shutdown_mid_session_still_closes_the_connection():
    setup = Setup()

    def interrupted() -> list[dict[str, object]]:
        raise Shutdown

    setup.client.serverlist = interrupted

    with pytest.raises(Shutdown):
        setup.service.poll()

    assert setup.client.closed


def test_sigterm_raises_shutdown():
    previous = signal.getsignal(signal.SIGTERM)
    try:
        handle_termination()
        handler = signal.getsignal(signal.SIGTERM)

        with pytest.raises(Shutdown):
            handler(signal.SIGTERM, None)
    finally:
        signal.signal(signal.SIGTERM, previous)


def test_the_password_never_reaches_the_metrics():
    # A hostile server reports the password it was just sent as a name.
    setup = Setup(
        servers=[
            {'virtualserver_id': 1, 'virtualserver_name': f'First {CONFIG.password}'},
            {'virtualserver_id': 2, 'virtualserver_name': CONFIG.password},
        ],
        missing={'virtualserver_uptime'},
    )

    setup.service.poll()

    exposition = generate_latest(setup.registry).decode()
    assert CONFIG.password not in exposition
    assert 'virtualserver_name="First *censored*"' in exposition
    assert 'teamspeak_exporter_missing_fields{virtualserver_name="*censored*"}' in (
        exposition
    )


def test_the_password_never_reaches_the_log(caplog):
    setup = Setup(login_succeeds=False)

    setup.service.poll()

    assert caplog.records
    assert CONFIG.password not in caplog.text


def test_every_password_given_is_censored_in_labels():
    # --ts3password old overridden by TEAMSPEAK_PASSWORD new: both are secrets
    registry = CollectorRegistry()
    client = FakeTs3Client(
        servers=[{'virtualserver_id': 1, 'virtualserver_name': 'Server old-secret'}]
    )
    service = Teamspeak3MetricService(
        CONFIG,
        build_gauges(registry),
        build_exporter_metrics(registry),
        factory_for(client),
        secrets=['old-secret', CONFIG.password],
    )

    service.poll()

    exposition = generate_latest(registry).decode()
    assert 'old-secret' not in exposition
    assert 'virtualserver_name="Server *censored*"' in exposition


def test_the_configured_password_is_always_a_secret():
    registry = CollectorRegistry()
    client = FakeTs3Client(
        servers=[{'virtualserver_id': 1, 'virtualserver_name': CONFIG.password}]
    )
    service = Teamspeak3MetricService(
        CONFIG,
        build_gauges(registry),
        build_exporter_metrics(registry),
        factory_for(client),
        secrets=['something-else'],
    )

    service.poll()

    assert CONFIG.password not in generate_latest(registry).decode()


class SteppedClocks:
    """A wall clock that NTP steps during the poll, and a monotonic clock that
    just advances by the real elapsed time."""

    def __init__(self, step: float, elapsed: float = 1.5):
        self.wall = 1_700_000_000.0
        self.monotonic = 500.0
        self.step = step
        self.elapsed = elapsed

    def factory(self, client: FakeTs3Client):
        def make(host: str, port: int) -> FakeTs3Client:
            self.wall += self.step  # the clock is stepped mid-poll
            self.wall += self.elapsed
            self.monotonic += self.elapsed
            return client

        return make


@pytest.mark.parametrize('step', [-3600.0, -1.0, 0.0, 3600.0])
def test_the_poll_duration_ignores_a_stepped_wall_clock(step):
    clocks = SteppedClocks(step)
    registry = CollectorRegistry()
    started = clocks.wall
    service = Teamspeak3MetricService(
        CONFIG,
        build_gauges(registry),
        build_exporter_metrics(registry),
        clocks.factory(FakeTs3Client()),
        clock=lambda: clocks.wall,
        monotonic=lambda: clocks.monotonic,
    )

    service.poll()

    def value(name: str) -> float | None:
        return registry.get_sample_value(name)

    assert value('teamspeak_exporter_poll_duration_seconds') == clocks.elapsed
    # timestamps stay Unix time from the wall clock, taken at the start
    assert value('teamspeak_exporter_last_poll_timestamp_seconds') == started
    assert value('teamspeak_exporter_last_successful_poll_timestamp_seconds') == started


def test_the_service_uses_the_monotonic_clock_by_default():
    import inspect

    parameters = inspect.signature(Teamspeak3MetricService).parameters

    assert parameters['monotonic'].default is time.monotonic
    assert parameters['clock'].default is time.time
