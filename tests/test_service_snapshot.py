"""A poll records all of its readings, or none -- and keeps them small."""

from __future__ import annotations

from prometheus_client import generate_latest

from teamspeak_prometheus.metrics import (
    MAX_LABEL_LENGTH,
)
from teamspeak_prometheus.service import PollResult
from tests.service_support import BEFORE, CONFIG, Setup, uptimes


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
