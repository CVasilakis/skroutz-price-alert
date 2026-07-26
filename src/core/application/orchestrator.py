"""Target coordination for a scraping run."""

from __future__ import annotations

import datetime
import logging
import signal
from collections.abc import Callable
from types import FrameType

from core.application.contracts import ConfigOutcome, RunOutcome, RunReporter
from core.application.diagnostics import record_target_load_diagnostic
from core.application.preflight import TargetConfigLoad
from core.application.reporting import SilentRunReporter
from core.application.target import TargetRunner
from core.infrastructure.locking import acquire_lock
from core.infrastructure.logging import get_target_logger, save_traceback
from core.infrastructure.signals import describe_signal
from core.notifications.contracts import NotificationService
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.settings import KEY_RETENTION
from core.scrapers.framework.state import JsonStateRepository


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ScrapingOrchestrator:
    """Coordinate target startup/delegation, summaries, interruption, and final status."""

    def __init__(
        self,
        target_loads: list[TargetConfigLoad],
        client_loader: ClientLoader,
        notifier: NotificationService,
        quiet: bool = False,
        reporter: RunReporter | None = None,
        now_fn: Callable[[], datetime.datetime] = _utc_now,
        state_dir: str = "state",
        state_repository_factory: Callable[..., JsonStateRepository] = JsonStateRepository,
    ) -> None:
        self.target_loads = tuple(target_loads)
        self.client_loader = client_loader
        self.notifier = notifier
        self.quiet = quiet
        self.reporter = reporter or SilentRunReporter()
        self.now_fn = now_fn
        self.state_dir = state_dir
        self.state_repository_factory = state_repository_factory
        self.interrupted = False
        self._interrupt_message = ""
        self._current_target = ""
        self._current_logger: logging.Logger | None = None

    def signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        self._interrupt_message = f"Received signal {describe_signal(signum)}"
        self.interrupted = True

    def _start_target(self, load: TargetConfigLoad) -> logging.Logger:
        plugin = load.plugin
        logger = get_target_logger(
            plugin.target,
            self.quiet,
            load.settings[plugin.setting(KEY_RETENTION)],
        )
        diagnostic_saved = record_target_load_diagnostic(load)
        config_error = load.failure.detail if load.failure is not None else None
        self.reporter.start_target(
            plugin.display_name,
            logger,
            load.settings,
            ConfigOutcome(
                load.count,
                tuple(load.faulty_indices),
                config_error,
                f"config/{plugin.config_filename}",
                diagnostic_saved,
            ),
        )
        return logger

    def run(self) -> int:
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
        outcome = RunOutcome()

        for load in self.target_loads:
            if self.interrupted:
                break
            plugin = load.plugin
            self._current_target = plugin.target
            self._current_logger = self._start_target(load)

            if load.failure is not None:
                outcome.target_config_error = True
                self.reporter.complete_target()
                continue

            target_runner = TargetRunner(
                client_loader=self.client_loader,
                notifier=self.notifier,
                reporter=self.reporter,
                now_fn=self.now_fn,
                state_dir=self.state_dir,
                state_repository_factory=self.state_repository_factory,
                acquire_lock_fn=acquire_lock,
                save_traceback_fn=save_traceback,
            )
            target_outcome = target_runner.run(load, self._current_logger, lambda: self.interrupted)
            outcome.storage_error |= target_outcome.storage_error
            outcome.dependency_error |= target_outcome.dependency_error
            outcome.scrape_error |= target_outcome.scrape_error
            outcome.rate_limited |= target_outcome.rate_limited
            outcome.notification_error |= target_outcome.notification_error
            outcome.skipped_count += int(target_outcome.skipped)

            if self.interrupted:
                self.reporter.log_interrupt(self._interrupt_message)
            self.reporter.complete_target()

        return outcome.exit_code(
            interrupted=self.interrupted,
            target_count=len(self.target_loads),
        )


__all__ = ["ScrapingOrchestrator"]
