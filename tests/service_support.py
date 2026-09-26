"""Shared setup of the service tests: a service around a fake client."""

from __future__ import annotations

from prometheus_client import CollectorRegistry

from teamspeak_prometheus.config import Config
from teamspeak_prometheus.metrics import (
    METRICS_NAMES,
    build_exporter_metrics,
    build_gauges,
)
from teamspeak_prometheus.service import Teamspeak3MetricService
from tests.fakes import FakeTs3Client, factory_for

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


def same_name(count: int = 2) -> list[dict[str, object]]:
    return [
        {'virtualserver_id': sid, 'virtualserver_name': 'TeamSpeak ]I[ Server'}
        for sid in range(1, count + 1)
    ]


def uptimes(setup: Setup) -> dict[str, float | None]:
    return {
        name: setup.value('teamspeak_virtualserver_uptime', virtualserver_name=name)
        for name in ('First', 'Second')
    }


UPTIME = METRICS_NAMES.index('virtualserver_uptime')


BEFORE = {'First': 1000.0 + UPTIME, 'Second': 2000.0 + UPTIME}
