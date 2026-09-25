from __future__ import annotations

import pytest
from prometheus_client import CollectorRegistry

from teamspeak_prometheus.metrics import (
    ExporterMetrics,
    build_exporter_metrics,
    build_gauges,
)
from tests.fakes import FakeTs3Client, serverinfo


@pytest.fixture
def registry() -> CollectorRegistry:
    """A registry per test. The global default one rejects duplicate gauges."""

    return CollectorRegistry()


@pytest.fixture
def gauges(registry: CollectorRegistry) -> dict[str, object]:
    return build_gauges(registry)


@pytest.fixture
def exporter_metrics(registry: CollectorRegistry) -> ExporterMetrics:
    return build_exporter_metrics(registry)


@pytest.fixture
def sample_serverinfo() -> dict[str, object]:
    return serverinfo('Test Server')


@pytest.fixture
def fake_client() -> FakeTs3Client:
    return FakeTs3Client()
