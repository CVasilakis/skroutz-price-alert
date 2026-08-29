"""One target's lock, client, execution, notification, and state lifecycle."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from core import messages
from core.application.contracts import RunOutcome, RunReporter
from core.application.items import ItemExecutor
from core.application.preflight import TargetConfigLoad
from core.constants import STALE_ITEM_HOURS
from core.exceptions import (
    LockAcquisitionError,
    LockStorageError,
    PluginDependencyError,
    StateFileError,
    StorageFileError,
)
from core.exit_status import ExitStatus
from core.infrastructure.logging import try_save_diagnostic, try_save_traceback
from core.notifications.contracts import NotificationService
from core.scrapers.api import TrackedItem
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.setting_specs import KEY_NOTIFY, KEY_SUPPRESS_REPEATED_PRICE_ALERTS
from core.scrapers.framework.state import JsonStateRepository


class TargetRunner:
    """Own exactly one target's lock/client/state lifecycle and one state commit."""

    def __init__(
        self,
        *,
        client_loader: ClientLoader,
        notifier: NotificationService,
        reporter: RunReporter,
        now_fn: Callable[[], datetime.datetime],
        state_dir: str,
        state_repository_factory: Callable[..., JsonStateRepository] = JsonStateRepository,
        acquire_lock_fn: Callable[[str], AbstractContextManager[Any]],
    ) -> None:
        self.client_loader = client_loader
        self.notifier = notifier
        self.reporter = reporter
        self.now_fn = now_fn
        self.state_dir = Path(state_dir)
        self.state_repository_factory = state_repository_factory
        self.acquire_lock_fn = acquire_lock_fn

    def _try_notification(
        self,
        operation: Callable[[], bool],
        *,
        logger: logging.Logger,
        target: str,
    ) -> bool:
        try:
            return bool(operation())
        except Exception:
            try_save_traceback(logger, target_name=target, log_to_console=False)
            return False

    def _report_storage_error(
        self,
        summary: str,
        exc: StorageFileError,
        *,
        target: str,
    ) -> None:
        diagnostic_saved = None
        if exc.diagnostic_detail:
            diagnostic_saved = try_save_diagnostic(
                exc.diagnostic_detail,
                target_name=target,
            )
        notes: str | list[str] = str(exc)
        if diagnostic_saved is False:
            notes = [str(exc), messages.DIAGNOSTIC_WRITE_FAILED]
        self.reporter.log_storage_error(summary, notes)

    def run(
        self,
        load: TargetConfigLoad,
        logger: logging.Logger,
        interrupted: Callable[[], bool],
    ) -> RunOutcome:
        """Run one target under its lock, owning the client and state lifecycle.

        The whole target is one bracket: acquire the lock, load state, build the
        client, execute every item, commit state once, then close the client in a
        ``finally`` and release the lock. Ordering matters — state is committed
        before the client is closed, so a shutdown fault cannot cost a run its
        results, and the client is closed even when the run failed or was
        interrupted.

        Every failure is converted to a condition on the returned outcome rather
        than propagated, so one target can never end the run: contention becomes a
        skip, storage and dependency faults become their own statuses, and an
        unexpected fault is logged with a traceback as a lifecycle failure.

        Returns:
            This target's conditions, for the run to merge.
        """
        plugin = load.plugin
        result = RunOutcome()
        failed_items: list[tuple[TrackedItem, Exception]] = []
        executor: ItemExecutor | None = None
        try:
            with self.acquire_lock_fn(plugin.target):
                state = self.state_repository_factory(
                    self.state_dir / f"{plugin.target}.json",
                    display_path=f"state/{plugin.target}.json",
                )
                try:
                    state.load()
                except StateFileError as exc:
                    self._report_storage_error(
                        messages.state_load_failed(plugin.target),
                        exc,
                        target=plugin.target,
                    )
                    result.statuses.add(ExitStatus.STORAGE_ERROR)
                    return result
                client = None
                primary_error: Exception | None = None
                cleanup_error: Exception | None = None
                try:
                    if not load.items:
                        return result
                    client = self.client_loader.load(plugin, load.settings)
                    executor = ItemExecutor(
                        target=plugin.target,
                        display_name=plugin.display_name,
                        client=client,
                        state=state,
                        notifier=self.notifier,
                        reporter=self.reporter,
                        logger=logger,
                        interrupted=interrupted,
                        now_fn=self.now_fn,
                        suppress_repeated_price_alerts=load.settings[
                            plugin.setting(KEY_SUPPRESS_REPEATED_PRICE_ALERTS)
                        ],
                        reference_url=plugin.item_reference_url,
                    )
                    abort_target = False
                    for item in load.items:
                        if abort_target or interrupted():
                            break
                        item_outcome = executor.process(item)
                        if item_outcome.reported_error:
                            failed_items.append((item, item_outcome.reported_error))
                        abort_target |= item_outcome.abort_target
                        result.statuses.update(item_outcome.statuses)
                    if state.has_pending:
                        try:
                            state.save()
                        except StorageFileError as exc:
                            self._report_storage_error(
                                messages.state_save_failed(plugin.target),
                                exc,
                                target=plugin.target,
                            )
                            result.statuses.add(ExitStatus.STORAGE_ERROR)
                except Exception as exc:
                    primary_error = exc
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except Exception as exc:
                            cleanup_error = exc
                if primary_error is not None:
                    preserved = primary_error.with_traceback(primary_error.__traceback__)
                    if cleanup_error is not None:
                        raise preserved from cleanup_error
                    raise preserved
                if cleanup_error is not None:
                    raise cleanup_error

            stale_items = executor.stale_items if executor is not None else []
            if not interrupted() and stale_items and self.notifier.has_services:
                delivered = self._try_notification(
                    lambda: self.notifier.notify_stale_items(
                        plugin.display_name,
                        stale_items,
                        STALE_ITEM_HOURS,
                        plugin.item_reference_url,
                    ),
                    logger=logger,
                    target=plugin.target,
                )
                if not delivered:
                    result.statuses.add(ExitStatus.NOTIFICATION_ERROR)
                    self.reporter.log_warning(
                        "Notifications", messages.WARN_STALE_NOTIFICATION_FAILED
                    )
            if not interrupted() and failed_items:
                notify_errors = load.settings[plugin.setting(KEY_NOTIFY)]
                if notify_errors and self.notifier.has_services:
                    delivered = self._try_notification(
                        lambda: self.notifier.notify_errors(plugin.display_name, failed_items),
                        logger=logger,
                        target=plugin.target,
                    )
                    if not delivered:
                        result.statuses.add(ExitStatus.NOTIFICATION_ERROR)
                        self.reporter.log_warning(
                            "Notifications", messages.WARN_ERROR_NOTIFICATION_FAILED
                        )
        except LockAcquisitionError:
            self.reporter.log_system_error(messages.ERR_LOCK_HELD)
            result.skipped_count = 1
        except LockStorageError as exc:
            self._report_storage_error(
                messages.lock_storage_failed(),
                exc,
                target=plugin.target,
            )
            result.statuses.add(ExitStatus.STORAGE_ERROR)
        except PluginDependencyError as exc:
            self.reporter.log_system_error(str(exc))
            result.statuses.add(ExitStatus.PLUGIN_DEPENDENCY_ERROR)
        except Exception as exc:
            try_save_traceback(logger, target_name=plugin.target, log_to_console=False)
            self.reporter.log_error(
                "Scraper",
                messages.plugin_lifecycle_failed(type(exc).__name__),
                messages.errors_log_pointer(plugin.target),
            )
            result.statuses.add(ExitStatus.SCRAPE_ERROR)
        return result


__all__ = ["TargetRunner"]
