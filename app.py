"""TeamSpeak 3 ServerQuery metrics exporter for Prometheus.

The module is importable without side effects: argument parsing, the metrics
HTTP server, and the polling loop all live behind ``main()``. See AGENTS.md.
"""

from __future__ import annotations

import argparse
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from prometheus_client import REGISTRY, CollectorRegistry, Gauge, start_http_server

READ_INTERVAL_IN_SECONDS = 5
METRICS_PREFIX = 'teamspeak_'
VIRTUALSERVER_LABEL = 'virtualserver_name'

DEFAULT_TS3_HOST = 'localhost'
DEFAULT_TS3_PORT = 10011
DEFAULT_TS3_USERNAME = 'serveradmin'
DEFAULT_TS3_PASSWORD = ''
DEFAULT_METRICS_PORT = 8000

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


class ExporterError(Exception):
    """Base class for errors raised by this exporter."""


class LoginFailed(ExporterError):
    """The ServerQuery login was rejected."""


class Ts3Client(Protocol):
    """The subset of the TeamSpeak client surface this exporter uses."""

    def login(self, username: str, password: str) -> bool: ...

    def serverlist(self) -> Any: ...

    def use(self, virtualserver_id: Any) -> Any: ...

    def send_command(self, command: str) -> Any: ...

    def disconnect(self) -> None: ...


ClientFactory = Callable[[str, int], Ts3Client]


@dataclass(frozen=True)
class Config:
    """Resolved exporter configuration."""

    host: str
    port: int
    username: str
    password: str
    metrics_port: int


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--ts3host',
        help='Hostname or ip address of TS3 server',
        type=str,
        default=DEFAULT_TS3_HOST,
    )
    parser.add_argument(
        '--ts3port', help='Port of TS3 server', type=int, default=DEFAULT_TS3_PORT
    )
    parser.add_argument(
        '--ts3username',
        help='ServerQuery username of TS3 server',
        type=str,
        default=DEFAULT_TS3_USERNAME,
    )
    parser.add_argument(
        '--ts3password',
        help='ServerQuery password of TS3 server',
        type=str,
        default=DEFAULT_TS3_PASSWORD,
    )
    parser.add_argument(
        '--metricsport',
        help='Port on which this service exposes the metrics',
        type=int,
        default=DEFAULT_METRICS_PORT,
    )
    return parser.parse_args(argv)


def resolve_config(args: argparse.Namespace, env: Mapping[str, str]) -> Config:
    """Combine parsed arguments with the environment.

    Environment variables win over command-line arguments, which is the
    behavior this exporter has always had.
    """

    return Config(
        host=env.get('TEAMSPEAK_HOST', args.ts3host),
        port=_port('TEAMSPEAK_PORT', env.get('TEAMSPEAK_PORT'), args.ts3port),
        username=env.get('TEAMSPEAK_USERNAME', args.ts3username),
        password=env.get('TEAMSPEAK_PASSWORD', args.ts3password),
        metrics_port=_port('METRICS_PORT', env.get('METRICS_PORT'), args.metricsport),
    )


def _port(name: str, value: str | None, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        return int(value)
    except ValueError as err:
        raise ExporterError(f'{name} must be a port number, got {value!r}') from err


def describe_settings(config: Config) -> str:
    """Render the startup banner. The password is never included."""

    return 'TS3 SETTINGS:\nHost: %s\nPort: %s\nUsername: %s\nPassword: *censored*' % (
        config.host,
        config.port,
        config.username,
    )


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
        print('Initialized gauge %s' % teamspeak_metric_name)
    return gauges


def update_gauges(gauges: Mapping[str, Gauge], serverinfo: Mapping[str, Any]) -> None:
    """Copy one ``serverinfo`` response into the gauges, unconverted."""

    virtualserver_name = serverinfo['virtualserver_name']
    for teamspeak_metric_name in METRICS_NAMES:
        gauges[teamspeak_metric_name].labels(
            **{VIRTUALSERVER_LABEL: virtualserver_name}
        ).set(serverinfo[teamspeak_metric_name])


def default_client_factory(host: str, port: int) -> Ts3Client:
    # Imported lazily so this module stays importable — and testable — without
    # the archived `ts3` package installed. See docs/modernization-backlog.md.
    import ts3

    return ts3.TS3Server(host, port)


class Teamspeak3MetricService:
    """Reads ``serverinfo`` for every virtualserver and updates the gauges."""

    def __init__(
        self,
        config: Config,
        gauges: Mapping[str, Gauge],
        client_factory: ClientFactory = default_client_factory,
    ) -> None:
        self.config = config
        self.gauges = gauges
        self.client_factory = client_factory
        self.client: Ts3Client | None = None

    def connect(self) -> None:
        self.client = self.client_factory(self.config.host, self.config.port)
        if not self.client.login(self.config.username, self.config.password):
            raise LoginFailed('Login not successful')

    def read(self) -> None:
        if self.client is None:
            raise ExporterError('read() called before connect()')

        serverlist_response = self.client.serverlist()
        if serverlist_response.response['msg'] != 'ok':
            print(
                'Error retrieving serverlist: %s' % serverlist_response.response['msg']
            )
            return

        for server in serverlist_response.data:
            self.client.use(server.get('virtualserver_id'))
            serverinfo_response = self.client.send_command('serverinfo')
            if serverinfo_response.response['msg'] != 'ok':
                print(
                    'Error retrieving serverinfo: %s'
                    % serverinfo_response.response['msg']
                )
                return

            update_gauges(self.gauges, serverinfo_response.data[0])

    def disconnect(self) -> None:
        if self.client is None:
            return
        self.client.disconnect()
        self.client = None


def poll_forever(
    service: Teamspeak3MetricService,
    interval_in_seconds: float = READ_INTERVAL_IN_SECONDS,
    iterations: int | None = None,
) -> None:
    """Connect, read, disconnect, sleep — forever, or ``iterations`` times."""

    remaining = iterations
    while remaining is None or remaining > 0:
        print('Fetching metrics')
        service.connect()
        service.read()
        service.disconnect()
        time.sleep(interval_in_seconds)
        if remaining is not None:
            remaining -= 1


def main(argv: list[str] | None = None) -> int:
    config = resolve_config(parse_args(argv), os.environ)
    print(describe_settings(config))

    gauges = build_gauges(REGISTRY)
    start_http_server(config.metrics_port)
    print('Started metrics endpoint on port %s' % config.metrics_port)

    poll_forever(Teamspeak3MetricService(config, gauges))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
