"""Non-interactive file-logging reporter."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from core import messages
from core.run import ConfigOutcome, Notes, PriceOutcome, RunReporter
from core.settings import SettingView


class SilentRunReporter(RunReporter):
    """Report a run exclusively through its target logger."""

    def __init__(self) -> None:
        self.target_logger: logging.Logger | None = None

    @staticmethod
    def _format_notes_suffix(notes_list: list[str]) -> str:
        return "" if not notes_list else " " + " ".join(f"({note})" for note in notes_list)

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings_view: Sequence[SettingView] = (),
        config: ConfigOutcome | None = None,
    ) -> None:
        self.target_logger = target_logger
        if config is not None:
            if config.error:
                target_logger.warning(f"❗ Monitored Items: Failed ({config.error})")
            elif config.faulty_indices:
                indices = ", ".join(map(str, config.faulty_indices))
                target_logger.warning(
                    f"❗ Monitored Items: {config.loaded_count} loaded, "
                    f"{len(config.faulty_indices)} misconfigured "
                    f"(Problematic items found at JSON index: {indices}.)"
                )
            else:
                target_logger.info(f"🗄️  Monitored Items: {config.loaded_count} loaded")
        for view in settings_view:
            if view.has_warning:
                target_logger.warning(f"❗ {view.label}: {view.display_value} ({view.footnote})")
            else:
                suffix = " (default)" if view.is_default else ""
                target_logger.info(f"⚙️  {view.label}: {view.display_value}{suffix}")

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        pass

    def complete_scraping(self) -> None:
        pass

    def log_result(self, icon: str, name: str, value: str, notes: Notes = None,
                   attempt_notes: Notes = None) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.info(f"{icon} {name}: {value}{suffix}")

    def log_price_result(self, name: str, price: float | None, currency: str,
                         target: float, outcome: PriceOutcome, notes: Notes = None,
                         attempt_notes: Notes = None,
                         delivery_failed: bool = False) -> None:
        if self.target_logger:
            price_str = (
                messages.ROW_NO_MATCH
                if outcome is PriceOutcome.NO_MATCH or price is None
                else f"{price} {currency}"
            )
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            message = (
                f"{self._outcome_icon(outcome, delivery_failed)} {name}: {price_str} "
                f"(Target: {target} {currency}){suffix}"
            )
            (self.target_logger.warning if delivery_failed else self.target_logger.info)(message)

    def log_warning(self, name: str, warning_str: str, notes: Notes = None,
                    attempt_notes: Notes = None) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.warning(f"❗ {name}: {warning_str}{suffix}")

    def log_error(self, name: str, error_str: str, notes: Notes = None,
                  attempt_notes: Notes = None) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.error(f"❗ {name}: {error_str}{suffix}")

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        if self.target_logger:
            self.target_logger.warning(
                f"❗ {name}: Attempt {attempt}/{max_retries} FAILED ({detail})"
            )

    def log_failure(self, name: str, error_type: str, attempt_notes: Notes = None,
                    extra_notes: Notes = None) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(extra_notes))
            self.target_logger.error(f"❗ {name}: All attempts failed ({error_type}){suffix}")

    def start_sleep(self, total_delay: float, retry_attempt: int = 0,
                    max_retries: int = 0) -> None:
        pass

    def update_sleep(self, remaining: float) -> None:
        pass

    def complete_sleep(self, actual_delay: float) -> None:
        pass

    def complete_target(self) -> None:
        self.target_logger = None

    def log_interrupt(self, message: str) -> None:
        if self.target_logger:
            self.target_logger.info(f"🛑 {message}")
        else:
            logging.info(f"🛑 {message}")


__all__ = ["SilentRunReporter"]
