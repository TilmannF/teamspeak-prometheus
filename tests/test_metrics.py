"""The metric contract: names, prefix, label, and value mapping."""

from __future__ import annotations

from teamspeak_prometheus import __version__
from teamspeak_prometheus.metrics import (
    ERROR_REASONS,
    MAX_LABEL_LENGTH,
    METRICS_NAMES,
    contract_values,
    label_value,
    update_gauges,
)


def test_every_documented_metric_gets_a_gauge(gauges):
    assert sorted(gauges) == sorted(METRICS_NAMES)
    assert len(METRICS_NAMES) == 41


def test_metric_names_are_prefixed_and_labelled(registry, gauges):
    update_gauges(gauges, 'Test', _values())

    names = {sample.name for metric in registry.collect() for sample in metric.samples}
    assert 'teamspeak_virtualserver_clientsonline' in names
    for metric in registry.collect():
        assert metric.name.startswith('teamspeak_')
        for sample in metric.samples:
            assert set(sample.labels) == {'virtualserver_name'}


def test_values_are_passed_through_unconverted(registry, gauges, sample_serverinfo):
    update_gauges(gauges, 'Test Server', sample_serverinfo)

    for offset, metric in enumerate(METRICS_NAMES):
        value = registry.get_sample_value(
            'teamspeak_' + metric, {'virtualserver_name': 'Test Server'}
        )
        assert value == offset


def test_string_values_from_serverquery_are_accepted(registry, gauges):
    update_gauges(gauges, 'Test', {m: '7' for m in METRICS_NAMES})

    assert (
        registry.get_sample_value(
            'teamspeak_virtualserver_uptime', {'virtualserver_name': 'Test'}
        )
        == 7
    )


def test_each_virtualserver_keeps_its_own_series(registry, gauges):
    update_gauges(gauges, 'A', _values(1))
    update_gauges(gauges, 'B', _values(2))

    label = 'teamspeak_virtualserver_clientsonline'
    assert registry.get_sample_value(label, {'virtualserver_name': 'A'}) == 1
    assert registry.get_sample_value(label, {'virtualserver_name': 'B'}) == 2


def test_missing_and_non_numeric_fields_are_skipped_not_raised(registry, gauges):
    values = _values(3)
    del values['virtualserver_uptime']
    values['virtualserver_total_ping'] = 'n/a'
    values['virtualserver_maxclients'] = None

    skipped = update_gauges(gauges, 'Test', values)

    assert skipped == [
        'virtualserver_maxclients',
        'virtualserver_total_ping',
        'virtualserver_uptime',
    ]
    assert (
        registry.get_sample_value(
            'teamspeak_virtualserver_clientsonline', {'virtualserver_name': 'Test'}
        )
        == 3
    )


def test_exporter_metrics_use_their_own_prefix(registry, exporter_metrics):
    names = {metric.name for metric in registry.collect()}

    assert names == {
        'teamspeak_exporter_build',
        'teamspeak_exporter_poll_errors',
        'teamspeak_exporter_last_poll_timestamp_seconds',
        'teamspeak_exporter_last_successful_poll_timestamp_seconds',
        'teamspeak_exporter_poll_success',
        'teamspeak_exporter_poll_duration_seconds',
        'teamspeak_exporter_missing_fields',
    }
    assert not {'exporter_' + name for name in METRICS_NAMES} & {
        name.removeprefix('teamspeak_') for name in names
    }


def test_build_info_carries_the_version(registry, exporter_metrics):
    assert (
        registry.get_sample_value(
            'teamspeak_exporter_build_info', {'version': __version__}
        )
        == 1
    )


def test_every_error_reason_is_exported_from_the_start(registry, exporter_metrics):
    for reason in ERROR_REASONS:
        assert (
            registry.get_sample_value(
                'teamspeak_exporter_poll_errors_total', {'reason': reason}
            )
            == 0
        )


def _values(value: int = 0) -> dict[str, object]:
    return {metric: value for metric in METRICS_NAMES}


# -- label values: a bounded length ----------------------------------------------


def test_a_normal_name_is_a_label_as_it_is():
    assert label_value('Zweiter Server') == 'Zweiter Server'


def test_a_name_at_the_limit_is_kept_and_one_beyond_is_cut():
    at_limit = 'x' * MAX_LABEL_LENGTH

    assert label_value(at_limit) == at_limit
    cut = label_value(at_limit + 'y')
    assert len(cut) == MAX_LABEL_LENGTH
    assert cut.endswith('…')


def test_contract_values_keep_only_the_41_numbers():
    serverinfo = {
        'virtualserver_name': 'x',
        'virtualserver_padding': 'p' * 100_000,
        'virtualserver_uptime': '42',
        'virtualserver_total_ping': 'n/a',
    }

    values = contract_values(serverinfo)

    assert values == {'virtualserver_uptime': 42.0}
    assert all(isinstance(v, float) for v in values.values())
