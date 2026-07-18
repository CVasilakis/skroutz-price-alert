"""Target coordination for a scraping run."""

from __future__ import annotations

import datetime
import logging
import signal
from collections.abc import Callable
from types import FrameType

from core import messages
from core.constants import OLD_ENTRY_HOURS
from core.exceptions import LockAcquisitionError, PluginDependencyError, StorageFileError
from core.execution import ItemExecutor
from core.locks import acquire_lock
from core.logger import get_target_logger, save_traceback
from core.notifier import Notifier
from core.preflight import TargetLoad
from core.reporting import SilentRunReporter
from core.run import ConfigOutcome, RunOutcome, RunReporter
from core.scrapers.registry import ClientFactory, setting_spec
from core.scrapers.settings import KEY_NOTIFY, KEY_RETENTION
from core.utils import describe_signal


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


class ScrapingOrchestrator:
    """Coordinate locks, clients, state commits, summaries, and final status."""

    def __init__(
        self,
        target_loads: list[TargetLoad],
        client_factory: ClientFactory,
        notifier: Notifier,
        quiet: bool = False,
        reporter: RunReporter | None = None,
        now_fn: Callable[[], datetime.datetime] = _utc_now,
    ) -> None:
        self.target_loads = tuple(target_loads)
        self.client_factory = client_factory
        self.notifier = notifier
        self.quiet = quiet
        self.reporter = reporter or SilentRunReporter()
        self.now_fn = now_fn
        self.interrupted = False
        self._interrupt_message = ""
        self._current_target = ""
        self._current_logger: logging.Logger | None = None

    def signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        self._interrupt_message = f"Received signal {describe_signal(signum)}"
        self.interrupted = True

    def _try_notification(self, operation: Callable[[], bool]) -> bool:
        try:
            return bool(operation())
        except Exception:
            if self._current_logger:
                save_traceback(
                    self._current_logger,
                    target_name=self._current_target,
                    log_to_console=False,
                )
            return False

    def _start_target(self, load: TargetLoad) -> logging.Logger:
        plugin = load.plugin
        logger = get_target_logger(
            plugin.target,
            self.quiet,
            load.settings[setting_spec(plugin, KEY_RETENTION)],
        )
        config_error = load.error if load.error is not None and not load.state_error else None
        self.reporter.start_target(
            plugin.display_name,
            logger,
            load.settings.views(),
            ConfigOutcome(load.count, tuple(load.faulty_indices), config_error),
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

            if load.error is not None:
                if load.state_error:
                    self.reporter.log_error(
                        "Storage",
                        messages.state_load_failed(plugin.target),
                        load.error,
                    )
                    outcome.storage_error = True
                else:
                    outcome.products_error = True
                self.reporter.complete_target()
                continue
            if not load.items:
                self.reporter.complete_target()
                continue
            assert load.state is not None

            failed_items = []
            abort_target = False
            try:
                with acquire_lock(plugin.target):
                    client = self.client_factory.create(plugin, load.settings)
                    executor = ItemExecutor(
                        target=plugin.target,
                        display_name=plugin.display_name,
                        client=client,
                        state=load.state,
                        notifier=self.notifier,
                        reporter=self.reporter,
                        logger=self._current_logger,
                        interrupted=lambda: self.interrupted,
                        now_fn=self.now_fn,
                    )
                    for item in load.items:
                        if abort_target or self.interrupted:
                            break
                        item_outcome = executor.process(item)
                        if item_outcome.reported_error:
                            failed_items.append((item, item_outcome.reported_error))
                        abort_target = abort_target or item_outcome.abort_target
                        outcome.rate_limited |= item_outcome.rate_limited
                        outcome.scrape_error |= item_outcome.affects_scrape_status
                        outcome.notification_error |= item_outcome.notification_failed
                    if load.state.has_pending:
                        try:
                            load.state.save()
                        except StorageFileError as exc:
                            self.reporter.log_error(
                                "Storage",
                                messages.state_save_failed(plugin.target),
                                str(exc),
                            )
                            outcome.storage_error = True

                if not self.interrupted and executor.stale_items and self.notifier.has_services:
                    delivered = self._try_notification(lambda: self.notifier.notify_old_entries(
                        plugin.display_name, executor.stale_items, OLD_ENTRY_HOURS
                    ))
                    if not delivered:
                        outcome.notification_error = True
                        self.reporter.log_warning(
                            "Notifications", messages.WARN_STALE_NOTIFICATION_FAILED
                        )
                if not self.interrupted and failed_items:
                    notify_errors = load.settings[setting_spec(plugin, KEY_NOTIFY)]
                    if notify_errors and self.notifier.has_services:
                        delivered = self._try_notification(lambda: self.notifier.notify_errors(
                            plugin.display_name, failed_items
                        ))
                        if not delivered:
                            outcome.notification_error = True
                            self.reporter.log_warning(
                                "Notifications", messages.WARN_ERROR_NOTIFICATION_FAILED
                            )
            except LockAcquisitionError:
                self.reporter.log_error("System", messages.ERR_LOCK_HELD)
                self.reporter.complete_target()
                outcome.skipped_count += 1
                continue
            except PluginDependencyError as exc:
                self.reporter.log_error("System", str(exc))
                self.reporter.complete_target()
                outcome.dependency_error = True
                continue

            if self.interrupted:
                self.reporter.log_interrupt(self._interrupt_message)
            self.reporter.complete_target()

        return outcome.exit_code(
            interrupted=self.interrupted,
            target_count=len(self.target_loads),
        )


__all__ = ["ScrapingOrchestrator"]
