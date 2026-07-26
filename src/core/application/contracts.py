"""Presentation-neutral contracts and outcomes for one scraping run."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_RATE_LIMIT_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_SUCCESS,
    EXIT_CODE_TARGET_CONFIG_ERROR,
)
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
    affects_scrape_status: bool = False
    notification_failed: bool = False
    abort_target: bool = False
    rate_limited: bool = False


@dataclass
class RunOutcome:
    target_config_error: bool = False
    storage_error: bool = False
    dependency_error: bool = False
    scrape_error: bool = False
    rate_limited: bool = False
    notification_error: bool = False
    skipped_count: int = 0

    def exit_code(self, *, interrupted: bool, target_count: int) -> int:
        if interrupted:
            return EXIT_CODE_INTERRUPT
        if self.target_config_error:
            return EXIT_CODE_TARGET_CONFIG_ERROR
        if self.storage_error:
            return EXIT_CODE_STORAGE_ERROR
        if self.dependency_error:
            return EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
        if self.scrape_error:
            return EXIT_CODE_SCRAPE_ERROR
        if self.rate_limited:
            return EXIT_CODE_RATE_LIMIT_ERROR
        if self.notification_error:
            return EXIT_CODE_NOTIFICATION_ERROR
        if self.skipped_count > 0 and self.skipped_count == target_count:
            return EXIT_CODE_SKIPPED
        return EXIT_CODE_SUCCESS


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
