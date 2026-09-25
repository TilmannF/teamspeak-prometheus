"""Polling: one ServerQuery session per poll, and the loop around it."""

from __future__ import annotations

import enum
import math
import time
from collections.abc import Callable, Mapping

from prometheus_client import Gauge

from teamspeak_prometheus.config import Config
from teamspeak_prometheus.errors import LoginFailed, ServerQueryError
from teamspeak_prometheus.logs import log
from teamspeak_prometheus.metrics import (
    VIRTUALSERVER_LABEL,
    ExporterMetrics,
    _remove_series,
    update_gauges,
)
from teamspeak_prometheus.redaction import redact, secrets_for_redaction
from teamspeak_prometheus.serverquery import ClientFactory, default_client_factory

# After this many consecutive failed polls the wait between attempts stops
# growing. Never shorter than the configured interval.
MAX_BACKOFF_IN_SECONDS = 60.0


class PollResult(enum.Enum):
    OK = 'ok'
    PARTIAL = 'partial'  # reached the server; some virtualservers failed
    FAILED = 'failed'  # could not connect, log in, or list virtualservers


class Teamspeak3MetricService:
    """Reads ``serverinfo`` for every virtualserver and updates the gauges.

    ``poll()`` never raises for anything that can go wrong on the network or
    in the server's answers: it logs, counts the error, and reports a result.
    """

    def __init__(
        self,
        config: Config,
        gauges: Mapping[str, Gauge],
        exporter_metrics: ExporterMetrics,
        client_factory: ClientFactory = default_client_factory,
        clock: Callable[[], float] = time.time,
        secrets: list[str] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        """``clock`` gives Unix time for the timestamp gauges; ``monotonic``
        measures the poll duration, which a stepped wall clock (NTP, an
        administrator) must not distort."""
        self.config = config
        self.gauges = gauges
        self.exporter_metrics = exporter_metrics
        self.client_factory = client_factory
        self.clock = clock
        self.monotonic = monotonic
        self.known_virtualservers: set[str] = set()
        self._warned_missing: set[tuple[str, str]] = set()
        # Label values come from the server and are untrusted, like its log
        # text: a hostile server could name a virtualserver after the password
        # it was just sent, and /metrics is unauthenticated. ``secrets`` is
        # every password given (main passes the log filter's list); the
        # configured one is always among them.
        self._secrets = secrets_for_redaction([config.password, *(secrets or [])])
        # virtualserver id -> status, for those serverlist reports not online
        self._not_online: dict[str, str] = {}
        # names shared by several virtualservers, already warned about
        self._warned_duplicates: set[str] = set()

    def poll(self) -> PollResult:
        metrics = self.exporter_metrics
        started = self.clock()
        started_monotonic = self.monotonic()
        metrics.last_poll_timestamp.set(started)
        try:
            result = self._poll()
        except LoginFailed as err:
            result = self._failed('login', 'ServerQuery login rejected: %s', err)
        except ServerQueryError as err:
            result = self._failed('query', 'ServerQuery error: %s', err)
        except OSError as err:
            result = self._failed(
                'connection',
                'Cannot reach ServerQuery at %s:%s: %s',
                self.config.host,
                self.config.port,
                err,
            )
        except Exception:
            log.exception('Unexpected error during poll')
            metrics.poll_errors.labels(reason='unexpected').inc()
            result = PollResult.FAILED

        metrics.poll_duration.set(self.monotonic() - started_monotonic)
        metrics.poll_success.set(1 if result is PollResult.OK else 0)
        if result is PollResult.OK:
            metrics.last_successful_poll_timestamp.set(started)
        return result

    def _failed(self, reason: str, message: str, *args: object) -> PollResult:
        log.error(message, *args)
        self.exporter_metrics.poll_errors.labels(reason=reason).inc()
        return PollResult.FAILED

    def _poll(self) -> PollResult:
        log.debug('Fetching metrics')
        client = self.client_factory(self.config.host, self.config.port)
        try:
            client.login(self.config.username, self.config.password)
            servers = client.serverlist()
            result = PollResult.OK
            seen: set[str] = set()
            not_online: dict[str, str] = {}
            owners: dict[str, list[str]] = {}
            for server in servers:
                virtualserver_id = server.get('virtualserver_id')
                status = server.get('virtualserver_status')
                if status not in (None, 'online'):
                    # Stopped (or booting, deploying, ...) is a state of the
                    # host, not an error of the exporter: skip it silently.
                    not_online[str(virtualserver_id)] = str(status)
                    continue
                try:
                    client.use(virtualserver_id)
                    serverinfo = client.serverinfo()
                except ServerQueryError as err:
                    log.warning('Skipping virtualserver %s: %s', virtualserver_id, err)
                    self.exporter_metrics.poll_errors.labels(reason='query').inc()
                    result = PollResult.PARTIAL
                    continue
                name = redact(
                    str(
                        serverinfo.get(VIRTUALSERVER_LABEL)
                        or server.get(VIRTUALSERVER_LABEL)
                        or f'virtualserver {virtualserver_id}'
                    ),
                    self._secrets,
                )
                self._record(name, serverinfo)
                seen.add(name)
                owners.setdefault(name, []).append(str(virtualserver_id))
            self._report_duplicates(owners)
            self._report_status_changes(not_online, servers)
            self._forget(self.known_virtualservers - seen)
            self.known_virtualservers = seen
            return result
        finally:
            client.close()

    def _report_duplicates(self, owners: dict[str, list[str]]) -> None:
        """Warn once per name shared by several virtualservers.

        ``virtualserver_name`` is the only label, so they write the same series
        and the last one read wins. Telling them apart needs a
        ``virtualserver_id`` label -- a breaking change, see
        docs/modernization-backlog.md.
        """

        duplicates = {name: ids for name, ids in owners.items() if len(ids) > 1}
        for name, ids in duplicates.items():
            if name not in self._warned_duplicates:
                log.warning(
                    "Virtualservers %s are all named '%s'; they share one set of "
                    'series and only the last one read is exported. Give them '
                    'distinct names.',
                    ', '.join(ids),
                    name,
                )
        self._warned_duplicates = set(duplicates)

    def _report_status_changes(
        self, not_online: dict[str, str], servers: list[dict[str, str | None]]
    ) -> None:
        """Log once when a virtualserver stops or starts being online."""

        names = {
            str(server.get('virtualserver_id')): server.get(VIRTUALSERVER_LABEL)
            for server in servers
        }
        for virtualserver_id, status in not_online.items():
            if self._not_online.get(virtualserver_id) != status:
                log.info(
                    "Virtualserver %s '%s' is %s; not exporting it",
                    virtualserver_id,
                    names.get(virtualserver_id),
                    status,
                )
        for virtualserver_id in self._not_online.keys() - not_online.keys():
            if virtualserver_id in names:
                log.info(
                    "Virtualserver %s '%s' is online again",
                    virtualserver_id,
                    names[virtualserver_id],
                )
        self._not_online = not_online

    def _record(self, name: str, serverinfo: Mapping[str, object]) -> None:
        skipped = update_gauges(self.gauges, name, serverinfo)
        self.exporter_metrics.missing_fields.labels(**{VIRTUALSERVER_LABEL: name}).set(
            len(skipped)
        )
        for field in skipped:
            if (name, field) not in self._warned_missing:
                self._warned_missing.add((name, field))
                log.warning(
                    "Virtualserver '%s': serverinfo field %s missing or not numeric; "
                    'not exporting it',
                    name,
                    field,
                )

    def _forget(self, virtualservers: set[str]) -> None:
        """Drop every series of virtualservers no longer read: deleted, not
        online, or failed this poll."""

        for name in virtualservers:
            log.info("Virtualserver '%s' was not read; removing its series", name)
            for gauge in self.gauges.values():
                _remove_series(gauge, name)
            _remove_series(self.exporter_metrics.missing_fields, name)
            self._warned_missing = {
                entry for entry in self._warned_missing if entry[0] != name
            }


def next_delay(interval: float, consecutive_failures: int) -> float:
    """Wait before the next poll: the interval, doubled per failure, capped.

    ``interval`` must be positive. Doubling continues until the cap for any
    positive interval, however small: the number of doublings is bounded by the
    number actually needed to reach the cap, and ``ldexp`` scales by powers of
    two without an intermediate result that could overflow.
    """

    if consecutive_failures == 0:
        return interval
    cap = max(interval, MAX_BACKOFF_IN_SECONDS)
    # log2(cap) - log2(interval), not log2(cap / interval): the quotient
    # overflows to infinity for a subnormal interval.
    needed = math.ceil(math.log2(cap) - math.log2(interval))
    return min(math.ldexp(interval, min(consecutive_failures, needed)), cap)


def poll_forever(
    service: Teamspeak3MetricService,
    interval_in_seconds: float,
    iterations: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Poll every ``interval_in_seconds``, start to start — forever, or
    ``iterations`` times. Backs off while polls fail outright."""

    failures = 0
    remaining = iterations
    while remaining is None or remaining > 0:
        started = clock()
        result = service.poll()
        failures = failures + 1 if result is PollResult.FAILED else 0
        if remaining is not None:
            remaining -= 1
            if remaining == 0:
                return
        sleep(max(next_delay(interval_in_seconds, failures) - (clock() - started), 0))
