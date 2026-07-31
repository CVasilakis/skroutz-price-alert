"""Presentation-neutral contracts and outcomes for one scraping run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from core.exit_status import ExitStatus
from core.scrapers.api import TrackedItem
from core.settings import ResolvedSettings

Notes = str | list[str] | None


class PriceOutcome(Enum):
    DROP = "drop"
    NO_TARGET = "no_target"
    OK = "ok"
    NO_MATCH = "no_match"


@dataclass(frozen=True)
class ConfigOutcome:
    loaded_count: int
    faulty_indices: tuple[int, ...] = ()
    error: str | None = None
    source_path: str | None = None
    diagnostic_saved: bool | None = None


@dataclass(frozen=True)
class ItemRunOutcome:
    item: TrackedItem
    reported_error: Exception | None = None
    statuses: frozenset[ExitStatus] = frozenset()
    abort_target: bool = False


@dataclass
class RunOutcome:
    """Mergeable run conditions and the policy for selecting one process status."""

    statuses: set[ExitStatus] = field(default_factory=set)
    skipped_count: int = 0

    def merge(self, other: RunOutcome) -> None:
        self.statuses.update(other.statuses)
        self.skipped_count += other.skipped_count

    def exit_status(self, *, interrupted: bool, target_count: int) -> ExitStatus:
        if interrupted:
            return ExitStatus.INTERRUPTED
        for status in (
            ExitStatus.APPLICATION_ERROR,
            ExitStatus.TARGET_CONFIG_ERROR,
            ExitStatus.NOTIFICATION_CONFIG_ERROR,
            ExitStatus.STORAGE_ERROR,
            ExitStatus.PLUGIN_DEPENDENCY_ERROR,
            ExitStatus.SCRAPE_ERROR,
            ExitStatus.RATE_LIMIT_ERROR,
            ExitStatus.NOTIFICATION_ERROR,
        ):
            if status in self.statuses:
                return status
        if self.skipped_count > 0 and self.skipped_count == target_count:
            return ExitStatus.ALREADY_RUNNING
        return ExitStatus.SUCCESS


class RunReporter(Protocol):
    """Core reporting protocol implemented by interactive and silent frontends."""

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings: ResolvedSettings,
        config: ConfigOutcome,
    ) -> None: ...

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None: ...

    def complete_scraping(self) -> None: ...

    def log_result(
        self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None: ...

    def log_price_result(
        self,
        name: str,
        price: float | None,
        currency: str,
        target: float,
        outcome: PriceOutcome,
        notes: Notes = None,
        attempt_notes: Notes = None,
        delivery_failed: bool = False,
    ) -> None: ...

    def log_warning(
        self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None: ...

    def log_error(
        self, name: str, error_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None: ...

    def log_system_error(self, error_str: str) -> None: ...

    def log_storage_error(self, summary: str, details: Notes = None) -> None: ...

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None: ...

    def log_failure(
        self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None
    ) -> None: ...

    def start_sleep(
        self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0
    ) -> None: ...

    def update_sleep(self, remaining: float) -> None: ...

    def complete_sleep(self, actual_delay: float) -> None: ...

    def complete_target(self) -> None: ...

    def log_interrupt(self, message: str) -> None: ...


__all__ = [
    "ConfigOutcome",
    "ItemRunOutcome",
    "Notes",
    "PriceOutcome",
    "RunOutcome",
    "RunReporter",
]
