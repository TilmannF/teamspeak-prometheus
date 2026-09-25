"""The metrics: the 41 TeamSpeak passthrough gauges and the exporter's own."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Info

from teamspeak_prometheus import __version__
from teamspeak_prometheus.logs import log

METRICS_PREFIX = 'teamspeak_'
EXPORTER_PREFIX = 'teamspeak_exporter_'
VIRTUALSERVER_LABEL = 'virtualserver_name'

ERROR_REASONS = ('connection', 'login', 'query', 'unexpected')

# Public API. Renaming or removing an entry breaks every dashboard and alerting
# rule built on this exporter. See AGENTS.md, "The Metric Contract".
METRICS_NAMES = [
    'connection_bandwidth_received_last_minute_total',
    'connection_bandwidth_received_last_second_total',
    'connection_bandwidth_sent_last_minute_total',
    'connection_bandwidth_sent_last_second_total',
    'connection_bytes_received_control',
    'connection_bytes_received_keepalive',
    'connection_bytes_received_speech',
    'connection_bytes_received_total',
    'connection_bytes_sent_control',
    'connection_bytes_sent_keepalive',
    'connection_bytes_sent_speech',
    'connection_bytes_sent_total',
    'connection_filetransfer_bandwidth_received',
    'connection_filetransfer_bandwidth_sent',
    'connection_filetransfer_bytes_received_total',
    'connection_filetransfer_bytes_sent_total',
    'connection_packets_received_control',
    'connection_packets_received_keepalive',
    'connection_packets_received_speech',
    'connection_packets_received_total',
    'connection_packets_sent_control',
    'connection_packets_sent_keepalive',
    'connection_packets_sent_speech',
    'connection_packets_sent_total',
    'virtualserver_channelsonline',
    'virtualserver_client_connections',
    'virtualserver_clientsonline',
    'virtualserver_maxclients',
    'virtualserver_month_bytes_downloaded',
    'virtualserver_month_bytes_uploaded',
    'virtualserver_query_client_connections',
    'virtualserver_queryclientsonline',
    'virtualserver_reserved_slots',
    'virtualserver_total_bytes_downloaded',
    'virtualserver_total_bytes_uploaded',
    'virtualserver_total_packetloss_control',
    'virtualserver_total_packetloss_keepalive',
    'virtualserver_total_packetloss_speech',
    'virtualserver_total_packetloss_total',
    'virtualserver_total_ping',
    'virtualserver_uptime',
]


def build_gauges(registry: CollectorRegistry) -> dict[str, Gauge]:
    """Create one labelled gauge per TeamSpeak metric.

    The registry is explicit because building twice against the global default
    registry raises ``Duplicated timeseries``, which would break the tests.
    """

    gauges: dict[str, Gauge] = {}
    for teamspeak_metric_name in METRICS_NAMES:
        gauges[teamspeak_metric_name] = Gauge(
            METRICS_PREFIX + teamspeak_metric_name,
            METRICS_PREFIX + teamspeak_metric_name,
            [VIRTUALSERVER_LABEL],
            registry=registry,
        )
        log.debug('Initialized gauge %s', teamspeak_metric_name)
    return gauges


@dataclass(frozen=True)
class ExporterMetrics:
    """The exporter's own health, next to the TeamSpeak passthrough gauges."""

    last_poll_timestamp: Gauge
    last_successful_poll_timestamp: Gauge
    poll_success: Gauge
    poll_duration: Gauge
    poll_errors: Counter
    missing_fields: Gauge


def build_exporter_metrics(registry: CollectorRegistry) -> ExporterMetrics:
    Info(
        EXPORTER_PREFIX + 'build',
        'Version of teamspeak-prometheus',
        registry=registry,
    ).info({'version': __version__})
    poll_errors = Counter(
        EXPORTER_PREFIX + 'poll_errors',
        'Failed ServerQuery polls or poll steps, by reason',
        ['reason'],
        registry=registry,
    )
    for reason in ERROR_REASONS:
        poll_errors.labels(reason=reason)
    return ExporterMetrics(
        last_poll_timestamp=Gauge(
            EXPORTER_PREFIX + 'last_poll_timestamp_seconds',
            'Unix time the last poll started',
            registry=registry,
        ),
        last_successful_poll_timestamp=Gauge(
            EXPORTER_PREFIX + 'last_successful_poll_timestamp_seconds',
            'Unix time the last fully successful poll started',
            registry=registry,
        ),
        poll_success=Gauge(
            EXPORTER_PREFIX + 'poll_success',
            '1 if the last poll read every virtualserver without error, else 0',
            registry=registry,
        ),
        poll_duration=Gauge(
            EXPORTER_PREFIX + 'poll_duration_seconds',
            'Duration of the last poll',
            registry=registry,
        ),
        poll_errors=poll_errors,
        missing_fields=Gauge(
            EXPORTER_PREFIX + 'missing_fields',
            'Expected serverinfo fields that were missing or not numeric in the '
            'last poll; their teamspeak_* series are not exported',
            [VIRTUALSERVER_LABEL],
            registry=registry,
        ),
    )


def update_gauges(
    gauges: Mapping[str, Gauge],
    virtualserver_name: str,
    serverinfo: Mapping[str, object],
) -> list[str]:
    """Copy one ``serverinfo`` response into the gauges, unconverted.

    A field that is missing or not a number is skipped, and its series removed
    so a stale value does not linger. Returns the skipped field names.
    """

    skipped = []
    for teamspeak_metric_name in METRICS_NAMES:
        gauge = gauges[teamspeak_metric_name]
        try:
            value = float(serverinfo[teamspeak_metric_name])  # type: ignore[arg-type]
        except (KeyError, TypeError, ValueError):
            skipped.append(teamspeak_metric_name)
            _remove_series(gauge, virtualserver_name)
            continue
        gauge.labels(**{VIRTUALSERVER_LABEL: virtualserver_name}).set(value)
    return skipped


def _remove_series(metric: Gauge, virtualserver_name: str) -> None:
    with suppress(KeyError):
        metric.remove(virtualserver_name)
