"""End-to-end: the healthcheck's probe against real endpoints."""

from __future__ import annotations

import pytest

from teamspeak_prometheus import healthcheck
from tests.smoke_support import free_port, names, scrape

pytestmark = pytest.mark.smoke


def test_the_healthcheck_probe_accepts_a_live_metrics_endpoint(exporter):
    _, port = exporter
    scrape(port, names())

    healthcheck.probe(port)


def test_the_healthcheck_probe_rejects_a_closed_port():
    with pytest.raises(healthcheck.HealthcheckError, match='did not answer'):
        healthcheck.probe(free_port())


@pytest.mark.parametrize('variable', ['http_proxy', 'HTTP_PROXY', 'all_proxy'])
def test_the_healthcheck_probe_ignores_a_proxy_in_the_environment(
    exporter, monkeypatch, variable
):
    # Nothing listens on the proxy port: going through it would fail the probe.
    _, port = exporter
    scrape(port, names())
    monkeypatch.delenv('no_proxy', raising=False)
    monkeypatch.delenv('NO_PROXY', raising=False)
    monkeypatch.setenv(variable, f'http://127.0.0.1:{free_port()}')

    healthcheck.probe(port)
