"""The poll loop: interval start to start, backoff on failure, its cap."""

from __future__ import annotations

import pytest

from teamspeak_prometheus.config import MIN_POLL_INTERVAL_IN_SECONDS
from teamspeak_prometheus.loop import MAX_BACKOFF_IN_SECONDS, next_delay, poll_forever
from teamspeak_prometheus.service import PollResult


class Loop:
    """A fake clock and sleep for ``poll_forever``."""

    def __init__(self, poll_seconds: float = 0.0):
        self.now = 0.0
        self.sleeps: list[float] = []
        self.poll_seconds = poll_seconds

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class ScriptedService:
    def __init__(self, loop: Loop, results: list[PollResult]):
        self.loop = loop
        self.results = list(results)

    def poll(self) -> PollResult:
        self.loop.now += self.loop.poll_seconds
        return self.results.pop(0)


def test_the_interval_is_measured_start_to_start():
    loop = Loop(poll_seconds=1.5)
    service = ScriptedService(loop, [PollResult.OK] * 3)

    poll_forever(service, 5, iterations=3, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [3.5, 3.5]


def test_failed_polls_back_off_and_success_resets():
    loop = Loop()
    failed, ok = PollResult.FAILED, PollResult.OK
    service = ScriptedService(loop, [failed, failed, failed, ok, ok])

    poll_forever(service, 5, iterations=5, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [10, 20, 40, 5]


def test_a_partial_poll_does_not_back_off():
    loop = Loop()
    service = ScriptedService(loop, [PollResult.PARTIAL] * 2)

    poll_forever(service, 5, iterations=2, sleep=loop.sleep, clock=loop.clock)

    assert loop.sleeps == [5]


@pytest.mark.parametrize(
    ('interval', 'failures', 'expected'),
    [(5, 0, 5), (5, 1, 10), (5, 4, 60), (5, 50, 60), (120, 3, 120)],
)
def test_backoff_is_capped_but_never_below_the_interval(interval, failures, expected):
    assert next_delay(interval, failures) == expected


def test_backoff_reaches_the_cap_from_the_shortest_allowed_interval():
    delays = [next_delay(MIN_POLL_INTERVAL_IN_SECONDS, n) for n in range(1, 10)]

    assert delays == [2, 4, 8, 16, 32, 60, 60, 60, 60]


@pytest.mark.parametrize('interval', [1e-6, 1e-300, 5e-324])
def test_backoff_reaches_the_cap_even_from_tiny_intervals(interval):
    # Not configurable, but the backoff must not depend on that: it keeps
    # doubling until the cap instead of stopping after a fixed number of steps.
    assert next_delay(interval, 10_000) == MAX_BACKOFF_IN_SECONDS


@pytest.mark.parametrize('interval', [5e-324, 1, 59.9, 86400])
@pytest.mark.parametrize('failures', [1, 1_023, 1_024, 1_100, 10**9])
def test_backoff_never_overflows_or_exceeds_its_cap(interval, failures):
    delay = next_delay(interval, failures)

    assert interval < delay <= max(interval, MAX_BACKOFF_IN_SECONDS) or (
        delay == interval == 86400
    )
