from __future__ import annotations

import logging
import socket
import threading
import time

import pytest
from prometheus_client import CollectorRegistry

from teamspeak_prometheus.logs import log
from teamspeak_prometheus.metrics import (
    ExporterMetrics,
    build_exporter_metrics,
    build_gauges,
)
from tests.cli_support import ENVIRONMENT
from tests.fakes import FakeTs3Client, serverinfo
from tests.smoke_support import start_exporter, stop


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


@pytest.fixture
def exporter():
    process, port = start_exporter()
    try:
        yield process, port
    finally:
        if process.poll() is None:
            stop(process)


@pytest.fixture
def trickling_server():
    """A TCP server that greets like TeamSpeak, then sends one byte every
    0.2s forever, never ending the line."""

    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    stop_event = threading.Event()

    def serve() -> None:
        try:
            connection, _ = listener.accept()
        except OSError:  # closed before anyone connected
            return
        with connection:
            connection.sendall(b'TS3\n\rWelcome\n\r')
            while not stop_event.is_set():
                try:
                    connection.sendall(b'x')
                except OSError:
                    return
                time.sleep(0.2)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    yield listener.getsockname()[1]
    stop_event.set()
    listener.close()


@pytest.fixture
def clean_env(monkeypatch):
    """No exporter variables, and logging restored after ``main()`` reset it."""

    for variable in ENVIRONMENT:
        monkeypatch.delenv(variable, raising=False)
    root = logging.getLogger()
    handlers, level, filters = root.handlers[:], root.level, log.filters[:]
    yield monkeypatch
    root.handlers[:] = handlers
    root.setLevel(level)
    log.filters[:] = filters
