"""Runs the real exporter against the fake ServerQuery server.

Used by the smoke test and by ``make run-fake``. Everything in ``app`` runs
unmodified, including the real ServerQuery client; only the environment is
ignored, so a stray ``TEAMSPEAK_HOST`` cannot point a test at a real server.

    python -m tests.exporter_harness
"""

from __future__ import annotations

import argparse

from prometheus_client import CollectorRegistry, start_http_server

import app
from tests.fake_ts3_server import FakeTs3Server


def run(
    ts3_host: str,
    ts3_port: int,
    ts3_password: str,
    metrics_port: int,
    iterations: int | None = None,
    interval_in_seconds: float = app.DEFAULT_POLL_INTERVAL_IN_SECONDS,
) -> None:
    config = app.resolve_config(
        app.parse_args(
            [
                '--ts3host',
                ts3_host,
                '--ts3port',
                str(ts3_port),
                '--ts3password',
                ts3_password,
                '--metricsport',
                str(metrics_port),
                '--pollinterval',
                str(interval_in_seconds),
            ]
        ),
        env={},
    )
    app.configure_logging(config.log_level, secrets=[config.password])
    app.log.info(app.describe_settings(config))

    registry = CollectorRegistry()
    gauges = app.build_gauges(registry)
    exporter_metrics = app.build_exporter_metrics(registry)
    try:
        start_http_server(config.metrics_port, registry=registry)
    except OSError as err:
        raise SystemExit(
            f'Could not listen on port {config.metrics_port} ({err}).\n'
            'Something else is using it -- pass --metricsport to pick another.'
        ) from err
    app.log.info('Started metrics endpoint on port %s', config.metrics_port)

    service = app.Teamspeak3MetricService(config, gauges, exporter_metrics)
    app.poll_forever(service, config.poll_interval, iterations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ts3host', default='127.0.0.1')
    parser.add_argument('--ts3port', type=int, default=0, help='0 starts a fake server')
    parser.add_argument('--ts3password', default='fake-password')
    parser.add_argument('--metricsport', type=int, default=8000)
    parser.add_argument('--iterations', type=int, default=None)
    parser.add_argument(
        '--interval', type=float, default=app.DEFAULT_POLL_INTERVAL_IN_SECONDS
    )
    parser.add_argument('--virtualservers', type=int, default=2)
    parser.add_argument('--flood-limit', type=int, default=None)
    parser.add_argument('--flood-window', type=float, default=3.0)
    args = parser.parse_args(argv)

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
        print(f'Fake TS3 ServerQuery listening on {ts3_host}:{ts3_port}')

    print(f'Scrape it with: curl -s http://127.0.0.1:{args.metricsport}/metrics')
    try:
        run(
            ts3_host,
            ts3_port,
            args.ts3password,
            args.metricsport,
            args.iterations,
            args.interval,
        )
    except KeyboardInterrupt:
        pass
    finally:
        if fake is not None:
            fake.stop()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
