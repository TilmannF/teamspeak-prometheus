"""The polling service: poll sequence, error survival, self-metrics, series."""

from __future__ import annotations

import signal
import time

import pytest
from prometheus_client import CollectorRegistry, generate_latest

from teamspeak_prometheus.config import MIN_POLL_INTERVAL_IN_SECONDS, Config
from teamspeak_prometheus.errors import ServerQueryError
from teamspeak_prometheus.loop import MAX_BACKOFF_IN_SECONDS, next_delay, poll_forever
from teamspeak_prometheus.main import Shutdown, handle_termination
from teamspeak_prometheus.metrics import (
    MAX_LABEL_LENGTH,
    METRICS_NAMES,
    build_exporter_metrics,
    build_gauges,
)
from teamspeak_prometheus.service import PollResult, Teamspeak3MetricService
from tests.fakes import FakeTs3Client, factory_for, unreachable

CONFIG = Config(
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
        self.service = Teamspeak3MetricService(
            CONFIG,
            build_gauges(self.registry),
            build_exporter_metrics(self.registry),
            factory or factory_for(self.client),
            clock=lambda: self.now,
        )

    def value(self, name: str, **labels: str) -> float | None:
        return self.registry.get_sample_value(name, labels)

    def calls(self, kind: str) -> list[tuple[str, object]]:
        return [call for call in self.client.calls if call[0] == kind]


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


def same_name(count: int = 2) -> list[dict[str, object]]:
    return [
        {'virtualserver_id': sid, 'virtualserver_name': 'TeamSpeak ]I[ Server'}
        for sid in range(1, count + 1)
    ]


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


def test_an_unexpected_exception_does_not_escape_the_poll():
    def broken(host: str, port: int):
        raise RuntimeError('boom')

    setup = Setup(factory=broken)

    assert setup.service.poll() is PollResult.FAILED
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
    def __init__(self, loop: Loop, results: list[PollResult]):
        self.loop = loop
        self.results = list(results)

    def poll(self) -> PollResult:
        self.loop.now += self.loop.poll_seconds
        return self.results.pop(0)


def test_the_interval_is_measured_start_to_start():
    loop = Loop(poll_seconds=1.5)
    service = ScriptedService(loop, [PollResult.OK] * 3)

    poll_forever(service, 5, iterations=3, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [3.5, 3.5]


def test_failed_polls_back_off_and_success_resets():
    loop = Loop()
    failed, ok = PollResult.FAILED, PollResult.OK
    service = ScriptedService(loop, [failed, failed, failed, ok, ok])

    poll_forever(service, 5, iterations=5, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [10, 20, 40, 5]


def test_a_partial_poll_does_not_back_off():
    loop = Loop()
    service = ScriptedService(loop, [PollResult.PARTIAL] * 2)

    poll_forever(service, 5, iterations=2, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [5]


@pytest.mark.parametrize(
    ('interval', 'failures', 'expected'),
    [(5, 0, 5), (5, 1, 10), (5, 4, 60), (5, 50, 60), (120, 3, 120)],
)
def test_backoff_is_capped_but_never_below_the_interval(interval, failures, expected):
    assert next_delay(interval, failures) == expected


def test_backoff_reaches_the_cap_from_the_shortest_allowed_interval():
    delays = [next_delay(MIN_POLL_INTERVAL_IN_SECONDS, n) for n in range(1, 10)]

    assert delays == [2, 4, 8, 16, 32, 60, 60, 60, 60]


@pytest.mark.parametrize('interval', [1e-6, 1e-300, 5e-324])
def test_backoff_reaches_the_cap_even_from_tiny_intervals(interval):
    # Not configurable, but the backoff must not depend on that: it keeps
    # doubling until the cap instead of stopping after a fixed number of steps.
    assert next_delay(interval, 10_000) == MAX_BACKOFF_IN_SECONDS


@pytest.mark.parametrize('interval', [5e-324, 1, 59.9, 86400])
@pytest.mark.parametrize('failures', [1, 1_023, 1_024, 1_100, 10**9])
def test_backoff_never_overflows_or_exceeds_its_cap(interval, failures):
    delay = next_delay(interval, failures)

    assert interval < delay <= max(interval, MAX_BACKOFF_IN_SECONDS) or (
        delay == interval == 86400
    )


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


# -- poll duration: elapsed time, not wall-clock difference --------------------


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


# -- a poll writes all of its readings, or none ---------------------------------


def uptimes(setup: Setup) -> dict[str, float | None]:
    return {
        name: setup.value('teamspeak_virtualserver_uptime', virtualserver_name=name)
        for name in ('First', 'Second')
    }


UPTIME = METRICS_NAMES.index('virtualserver_uptime')
BEFORE = {'First': 1000.0 + UPTIME, 'Second': 2000.0 + UPTIME}


def test_a_connection_lost_mid_poll_keeps_the_previous_snapshot_whole():
    setup = Setup()
    setup.service.poll()
    setup.client.generation = 5
    setup.client.drop_on = 2  # First was already read when this happens

    assert setup.service.poll() is PollResult.FAILED

    assert uptimes(setup) == BEFORE  # not First new and Second old


def test_a_timeout_mid_poll_keeps_the_previous_snapshot_whole():
    setup = Setup()
    setup.service.poll()
    setup.client.generation = 5
    real_serverinfo = setup.client.serverinfo

    def slow() -> dict[str, object]:
        if setup.client._selected == 2:
            raise TimeoutError('ServerQuery session did not finish within 60s')
        return real_serverinfo()

    setup.client.serverinfo = slow

    assert setup.service.poll() is PollResult.FAILED
    assert uptimes(setup) == BEFORE


def test_nothing_is_written_while_the_poll_is_still_reading():
    setup = Setup()
    setup.service.poll()
    setup.client.generation = 5
    real_serverinfo = setup.client.serverinfo
    seen_mid_poll: list[dict[str, float | None]] = []

    def observed() -> dict[str, object]:
        if setup.client._selected == 2:  # First has been read by now
            seen_mid_poll.append(uptimes(setup))
        return real_serverinfo()

    setup.client.serverinfo = observed

    assert setup.service.poll() is PollResult.OK

    assert seen_mid_poll == [BEFORE]  # a scrape now would see one snapshot
    assert uptimes(setup) == {name: value + 5 for name, value in BEFORE.items()}


def test_a_failed_poll_also_leaves_missing_fields_and_removals_alone():
    setup = Setup()
    setup.service.poll()
    setup.client.missing = {'virtualserver_uptime'}
    setup.client.servers = setup.client.servers[:1] + [
        {'virtualserver_id': 3, 'virtualserver_name': 'Third'}
    ]
    setup.client.drop_on = 3

    setup.service.poll()

    # nothing of the failed poll landed: First keeps its uptime, Second (not
    # listed any more) is not removed, no missing-field count changed
    assert uptimes(setup) == BEFORE
    assert (
        setup.value('teamspeak_exporter_missing_fields', virtualserver_name='First')
        == 0
    )


def test_a_partial_poll_still_writes_everything_it_read():
    setup = Setup(offline={2})
    setup.client.generation = 5

    assert setup.service.poll() is PollResult.PARTIAL

    assert uptimes(setup) == {'First': BEFORE['First'] + 5, 'Second': None}


def test_the_poll_after_a_failed_one_writes_everything_new():
    setup = Setup()
    setup.service.poll()
    setup.client.generation = 5
    setup.client.drop_on = 2
    setup.service.poll()
    setup.client.drop_on = None

    assert setup.service.poll() is PollResult.OK

    assert uptimes(setup) == {name: value + 5 for name, value in BEFORE.items()}


# -- the staged snapshot stays small whatever the server sends --------------------


def test_staging_keeps_only_the_contract_values_not_the_responses():
    import tracemalloc

    from teamspeak_prometheus.serverquery import MAX_LINE_BYTES, ServerQueryClient
    from tests.fakes import FakeConnection

    count = 40
    ok = b'error id=0 msg=ok\n\r'
    pad = b'x' * (MAX_LINE_BYTES - 200)
    listed = b'|'.join(
        b'virtualserver_id=%d virtualserver_status=online' % sid
        for sid in range(1, count + 1)
    )
    replies = [b'TS3\n\rW\n\r', ok, listed + b'\n\r' + ok]
    for sid in range(1, count + 1):
        replies += [
            ok,
            b'virtualserver_name=s%d virtualserver_padding=' % sid + pad + b'\n\r' + ok,
        ]
    setup = Setup()
    client = ServerQueryClient(FakeConnection(*replies))
    client.login('serveradmin', 'x')

    tracemalloc.start()
    try:
        session = setup.service._read(client)
        held, _ = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert len(session.readings) == count
    assert held < 1024 * 1024  # 40 MiB were received; well under 1 MiB is kept


def test_a_long_name_is_cut_to_the_label_limit():
    setup = Setup(servers=[{'virtualserver_id': 1, 'virtualserver_name': 'n' * 5_000}])

    setup.service.poll()

    (label,) = [
        sample.labels['virtualserver_name']
        for metric in setup.registry.collect()
        if metric.name == 'teamspeak_virtualserver_uptime'
        for sample in metric.samples
    ]
    assert len(label) == MAX_LABEL_LENGTH
    assert label.endswith('…')


def test_a_password_across_the_cut_is_censored_before_cutting():
    # cutting first would leave the password's first half in the label
    name = 'n' * (MAX_LABEL_LENGTH - 4) + CONFIG.password
    setup = Setup(servers=[{'virtualserver_id': 1, 'virtualserver_name': name}])

    setup.service.poll()

    exposition = generate_latest(setup.registry).decode()
    # censored, then cut: the marker is cut, the password is gone entirely
    assert 'n' * (MAX_LABEL_LENGTH - 4) + '*ce…"' in exposition
    assert 'secr' not in exposition
