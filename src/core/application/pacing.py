"""Interruptible request pacing for item execution."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

from core.application.contracts import RunReporter
from core.constants import (
    MAX_RETRIES,
    RANDOM_DELAY_MAX,
    RANDOM_DELAY_MIN,
    RETRY_DELAY_MULTIPLIER,
)


class Pacer:
    """Apply base delay, retry backoff, and jitter without hiding interruption."""

    def __init__(
        self,
        reporter: RunReporter,
        interrupted: Callable[[], bool],
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
        jitter_fn: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self.reporter = reporter
        self.interrupted = interrupted
        self.monotonic_fn = monotonic_fn
        self.sleep_fn = sleep_fn
        self.jitter_fn = jitter_fn

    def sleep(self, base_delay: float, attempt: int = 0, *, is_retry: bool = False) -> None:
        jitter = self.jitter_fn(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
        total_delay = base_delay + RETRY_DELAY_MULTIPLIER * attempt + jitter
        start_time = self.monotonic_fn()
        retry_attempt = attempt + 2 if is_retry else 0
        self.reporter.start_sleep(total_delay, retry_attempt, MAX_RETRIES if is_retry else 0)
        while self.monotonic_fn() - start_time < total_delay:
            if self.interrupted():
                break
            remaining = max(0.0, total_delay - (self.monotonic_fn() - start_time))
            self.reporter.update_sleep(remaining)
            self.sleep_fn(0.05)
        if not self.interrupted():
            self.reporter.complete_sleep(self.monotonic_fn() - start_time)


__all__ = ["Pacer"]
