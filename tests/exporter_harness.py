"""Runs the real exporter against the fake ServerQuery server.

Used by the smoke test and by ``make run-fake``. Everything in ``app`` runs
unmodified, including the real ServerQuery client; only the environment is
ignored, so a stray ``TEAMSPEAK_HOST`` cannot point a test at a real server.

    python -m tests.exporter_harness
"""

from __future__ import annotations

import dataclasses

from prometheus_client import CollectorRegistry, start_http_server

from teamspeak_prometheus.cli import (
    RememberEveryValue,
    SafeArgumentParser,
    given_values,
    parse_args,
)
from teamspeak_prometheus.config import (
    DEFAULT_POLL_INTERVAL_IN_SECONDS,
    log_settings,
    resolve_config,
)
from teamspeak_prometheus.logs import configure_logging, log
from teamspeak_prometheus.metrics import build_exporter_metrics, build_gauges
from teamspeak_prometheus.redaction import printable, redact, secrets_for_redaction
from teamspeak_prometheus.service import Teamspeak3MetricService, poll_forever
from tests.fake_ts3_server import FakeTs3Server


def run(
    ts3_host: str,
    ts3_port: int,
    ts3_password: str,
    metrics_port: int,
    iterations: int | None = None,
    interval_in_seconds: float = DEFAULT_POLL_INTERVAL_IN_SECONDS,
    secrets: list[str] | None = None,
) -> None:
    """``secrets`` is every password given (all repeats of --ts3password);
    ``ts3_password`` is the one used."""

    secrets = secrets_for_redaction([ts3_password, *(secrets or [])])
    config = resolve_config(
        parse_args(
            [
                '--ts3host',
                ts3_host,
                '--ts3port',
                str(ts3_port),
                '--ts3password',
                ts3_password,
                '--metricsport',
                str(metrics_port),
            ]
        ),
        env={},
    )
    # Tests poll faster than the exporter's 1-second minimum allows. The
    # interval goes straight to the poll loop, past configuration validation;
    # the banner still shows the interval actually used.
    config = dataclasses.replace(config, poll_interval=interval_in_seconds)
    configure_logging(config.log_level, secrets=secrets)
    log_settings(config)

    registry = CollectorRegistry()
    gauges = build_gauges(registry)
    exporter_metrics = build_exporter_metrics(registry)
    try:
        # Loopback only: a local test tool has no business on the network.
        start_http_server(config.metrics_port, addr='127.0.0.1', registry=registry)
    except OSError as err:
        raise SystemExit(
            redact(
                f'Could not listen on port {config.metrics_port} ({err}).\n'
                'Something else is using it -- pass --metricsport to pick another.',
                secrets,
            )
        ) from err
    log.info('Started metrics endpoint on port %s', config.metrics_port)

    service = Teamspeak3MetricService(config, gauges, exporter_metrics, secrets=secrets)
    poll_forever(service, config.poll_interval, iterations)


def main(argv: list[str] | None = None) -> int:
    parser = SafeArgumentParser(description=__doc__, secret_options=('--ts3password',))
    parser.add_argument('--ts3host', default='127.0.0.1')
    parser.add_argument('--ts3port', type=int, default=0, help='0 starts a fake server')
    parser.add_argument(
        '--ts3password', default='fake-password', action=RememberEveryValue
    )
    parser.add_argument('--metricsport', type=int, default=8000)
    parser.add_argument('--iterations', type=int, default=None)
    parser.add_argument(
        '--interval', type=float, default=DEFAULT_POLL_INTERVAL_IN_SECONDS
    )
    parser.add_argument('--virtualservers', type=int, default=2)
    parser.add_argument('--flood-limit', type=int, default=None)
    parser.add_argument('--flood-window', type=float, default=3.0)
    args = parser.parse_args(argv)
    secrets = secrets_for_redaction(given_values(args, 'ts3password'))

    def say(text: str) -> None:
        # Everything printed is censored like the exporter's log: even a port
        # number is the password when someone picks it as one. See AGENTS.md.
        print(printable(text, secrets))

    fake = None
    ts3_host, ts3_port = args.ts3host, args.ts3port
    if not ts3_port:
        fake = FakeTs3Server(
            password=args.ts3password,
            virtualserver_count=args.virtualservers,
            flood_limit=args.flood_limit,
            flood_window=args.flood_window,
        ).start()
        ts3_host, ts3_port = fake.host, fake.port
        say(f'Fake TS3 ServerQuery listening on {ts3_host}:{ts3_port}')

    say(f'Scrape it with: curl -s http://127.0.0.1:{args.metricsport}/metrics')
    try:
        run(
            ts3_host,
            ts3_port,
            args.ts3password,
            args.metricsport,
            args.iterations,
            args.interval,
            secrets,
        )
    except KeyboardInterrupt:
        pass
    finally:
        if fake is not None:
            fake.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
