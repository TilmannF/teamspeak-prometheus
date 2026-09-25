"""Startup and shutdown: configuration, the metrics endpoint, the poll loop."""

from __future__ import annotations

import argparse
import os
import signal

from prometheus_client import REGISTRY, start_http_server

from teamspeak_prometheus.cli import parse_args, password_candidates
from teamspeak_prometheus.config import (
    DEFAULT_LOG_LEVEL,
    Config,
    log_settings,
    overridden_flags,
    resolve_config,
)
from teamspeak_prometheus.errors import ExporterError
from teamspeak_prometheus.logs import configure_logging, log
from teamspeak_prometheus.loop import poll_forever
from teamspeak_prometheus.metrics import build_exporter_metrics, build_gauges
from teamspeak_prometheus.service import Teamspeak3MetricService


class Shutdown(BaseException):
    """SIGTERM arrived. A ``BaseException``, like ``KeyboardInterrupt``, so the
    ``except Exception`` in ``poll()`` cannot swallow it."""


def _shut_down(signum: int, frame: object) -> None:
    raise Shutdown


def handle_termination() -> None:
    """Stop cleanly on SIGTERM.

    In a container the exporter is PID 1, and the kernel ignores a signal PID 1
    has no handler for: without this, ``docker stop`` waits its 10 seconds and
    then kills the process (exit code 137). The exception interrupts a poll
    wherever it is, even a blocked socket read, and the ``finally`` blocks
    still close the ServerQuery session.
    """

    signal.signal(signal.SIGTERM, _shut_down)


def main(argv: list[str] | None = None) -> int:
    # Redaction first: configuration errors quote the offending value, which
    # can equal the password. Every password given counts, used or not, and
    # the same list reaches every place that censors: argparse errors, the log
    # filter, and the service's metric labels.
    environment_secrets = password_candidates(argparse.Namespace(), os.environ)
    configure_logging(DEFAULT_LOG_LEVEL, secrets=environment_secrets)
    args = parse_args(argv, secrets=environment_secrets)
    secrets = password_candidates(args, os.environ)
    configure_logging(DEFAULT_LOG_LEVEL, secrets=secrets)
    try:
        config = resolve_config(args, os.environ)
    except ExporterError as err:
        log.error('Invalid configuration: %s', err)
        return 2
    configure_logging(config.log_level, secrets=secrets)
    for flag in overridden_flags(args, os.environ):
        log.warning('Ignoring %s: environment variables take precedence', flag)
    log_settings(config)

    handle_termination()
    try:
        return serve(config, secrets)
    except (KeyboardInterrupt, Shutdown):
        log.info('Stopped')
        return 0


def serve(config: Config, secrets: list[str]) -> int:
    """Expose the metrics and poll until interrupted."""

    gauges = build_gauges(REGISTRY)
    exporter_metrics = build_exporter_metrics(REGISTRY)
    try:
        start_http_server(config.metrics_port)
    except OSError as err:
        log.error('Cannot listen on port %s: %s', config.metrics_port, err)
        return 1
    log.info('Started metrics endpoint on port %s', config.metrics_port)

    service = Teamspeak3MetricService(config, gauges, exporter_metrics, secrets=secrets)
    poll_forever(service, config.poll_interval)
    return 0
