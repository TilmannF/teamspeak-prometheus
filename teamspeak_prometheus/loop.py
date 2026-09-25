"""The poll loop: one poll per interval, start to start, backing off on failure."""

from __future__ import annotations

import math
import time
from collections.abc import Callable

from teamspeak_prometheus.service import PollResult, Teamspeak3MetricService

# After this many consecutive failed polls the wait between attempts stops
# growing. Never shorter than the configured interval.
MAX_BACKOFF_IN_SECONDS = 60.0


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
