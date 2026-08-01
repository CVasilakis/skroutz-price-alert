"""Non-interactive file-logging reporter."""

from __future__ import annotations

import logging

from core import messages
from core.application.contracts import ConfigOutcome, Notes, PriceOutcome, RunReporter
from core.presentation import resolved_setting_views
from core.settings import ResolvedSettings


def _plain_text(value: str | None) -> str:
    """Render paired code spans distinctly in output channels without styling."""
    if value is None:
        return ""
    return value.replace("`", '"') if value.count("`") % 2 == 0 else value


class SilentRunReporter(RunReporter):
    """Report a run exclusively through its target logger."""

    def __init__(self) -> None:
        self.target_logger: logging.Logger | None = None

    @staticmethod
    def _format_notes_suffix(notes_list: list[str]) -> str:
        return "" if not notes_list else " " + " ".join(f"({note})" for note in notes_list)

    @staticmethod
    def _normalize_notes(notes: Notes) -> list[str]:
        if notes is None:
            return []

        def ensure_period(value: str) -> str:
            stripped = _plain_text(value).strip()
            return stripped + "." if stripped and not stripped.endswith(".") else stripped

        if isinstance(notes, str):
            return [ensure_period(notes)] if notes else []
        return [ensure_period(note) for note in notes if note]

    @staticmethod
    def _outcome_icon(outcome: PriceOutcome, delivery_failed: bool = False) -> str:
        if delivery_failed:
            return "🟡"
        return {PriceOutcome.DROP: "🎉", PriceOutcome.NO_TARGET: "🟡"}.get(outcome, "✅")

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings: ResolvedSettings,
        config: ConfigOutcome,
    ) -> None:
        self.target_logger = target_logger
        if config.error:
            detail = _plain_text(config.error)
            if config.diagnostic_saved is False:
                detail = f"{detail} {messages.DIAGNOSTIC_WRITE_FAILED}"
            target_logger.warning(f"❗ Tracked Items: Failed ({detail})")
        elif config.faulty_indices:
            detail = messages.misconfigured_items(config.source_path)
            if config.diagnostic_saved is False:
                detail = f"{detail} {messages.DIAGNOSTIC_WRITE_FAILED}"
            target_logger.warning(
                f"❗ Tracked Items: {config.loaded_count} loaded, "
                f"{len(config.faulty_indices)} misconfigured "
                f"({detail})"
            )
        else:
            target_logger.info(f"🗄️  Tracked Items: {config.loaded_count} loaded")
        for view in resolved_setting_views(settings):
            if view.has_warning:
                target_logger.warning(
                    f"❗ {view.label}: {view.display_value} ({_plain_text(view.footnote)})"
                )
            else:
                suffix = " (default)" if view.is_default else ""
                target_logger.info(f"⚙️  {view.label}: {view.display_value}{suffix}")

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        pass

    def complete_scraping(self) -> None:
        pass

    def log_result(
        self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.info(f"{icon} {name}: {_plain_text(value)}{suffix}")

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
    ) -> None:
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

    def log_warning(
        self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.warning(f"❗ {name}: {_plain_text(warning_str)}{suffix}")

    def log_error(
        self, name: str, error_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.error(f"❗ {name}: {_plain_text(error_str)}{suffix}")

    def log_system_error(self, error_str: str) -> None:
        """Log a target-start system failure with the established file-log wording."""
        self.log_error("System", error_str)

    def log_storage_error(self, summary: str, details: Notes = None) -> None:
        """Log a state-storage failure with the established file-log wording."""
        self.log_error("Storage", summary, details)

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        if self.target_logger:
            self.target_logger.warning(
                f"❗ {name}: Attempt {attempt}/{max_retries} FAILED ({_plain_text(detail)})"
            )

    def log_failure(
        self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None
    ) -> None:
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(extra_notes))
            self.target_logger.error(f"❗ {name}: All attempts failed ({error_type}){suffix}")

    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        pass

    def update_sleep(self, remaining: float) -> None:
        pass

    def complete_sleep(self, actual_delay: float) -> None:
        pass

    def complete_target(self) -> None:
        self.target_logger = None

    def log_interrupt(self, message: str) -> None:
        if self.target_logger:
            self.target_logger.info(f"🛑 {_plain_text(message)}")
        else:
            logging.info(f"🛑 {_plain_text(message)}")


__all__ = ["SilentRunReporter"]
