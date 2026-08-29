"""The metric contract: names, prefix, label, and value mapping."""

from __future__ import annotations

import app


def test_every_documented_metric_gets_a_gauge(gauges):
    assert sorted(gauges) == sorted(app.METRICS_NAMES)
    assert len(app.METRICS_NAMES) == 41


def test_metric_names_are_prefixed_and_labelled(registry, gauges):
    app.update_gauges(gauges, {'virtualserver_name': 'Test', **_values()})

    names = {sample.name for metric in registry.collect() for sample in metric.samples}
    assert 'teamspeak_virtualserver_clientsonline' in names
    for metric in registry.collect():
        assert metric.name.startswith('teamspeak_')
        for sample in metric.samples:
            assert set(sample.labels) == {'virtualserver_name'}


def test_values_are_passed_through_unconverted(registry, gauges, sample_serverinfo):
    app.update_gauges(gauges, sample_serverinfo)

    for offset, metric in enumerate(app.METRICS_NAMES):
        value = registry.get_sample_value(
            'teamspeak_' + metric, {'virtualserver_name': 'Test Server'}
        )
        assert value == offset


def test_string_values_from_serverquery_are_accepted(registry, gauges):
    app.update_gauges(
        gauges, {'virtualserver_name': 'Test', **{m: '7' for m in app.METRICS_NAMES}}
    )

    assert (
        registry.get_sample_value(
            'teamspeak_virtualserver_uptime', {'virtualserver_name': 'Test'}
        )
        == 7
    )


def test_each_virtualserver_keeps_its_own_series(registry, gauges):
    app.update_gauges(gauges, {'virtualserver_name': 'A', **_values(1)})
    app.update_gauges(gauges, {'virtualserver_name': 'B', **_values(2)})

    label = 'teamspeak_virtualserver_clientsonline'
    assert registry.get_sample_value(label, {'virtualserver_name': 'A'}) == 1
    assert registry.get_sample_value(label, {'virtualserver_name': 'B'}) == 2


def _values(value: int = 0) -> dict[str, int]:
    return {metric: value for metric in app.METRICS_NAMES}
