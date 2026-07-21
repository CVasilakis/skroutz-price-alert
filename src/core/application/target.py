"""One target's lock, client, execution, notification, and state lifecycle."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from core import messages
from core.application.contracts import RunReporter
from core.application.items import ItemExecutor
from core.application.preflight import TargetLoad
from core.constants import OLD_ENTRY_HOURS
from core.exceptions import LockAcquisitionError, PluginDependencyError, StorageFileError
from core.infrastructure.locking import acquire_lock
from core.infrastructure.logging import save_traceback
from core.notifications.contracts import NotificationService
from core.scrapers.api import TrackedItem
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.settings import KEY_NOTIFY


@dataclass
class TargetRunOutcome:
    """Target-local facts merged into the run-wide exit decision."""

    storage_error: bool = False
    dependency_error: bool = False
    scrape_error: bool = False
    rate_limited: bool = False
    notification_error: bool = False
    skipped: bool = False


class TargetRunner:
    """Own exactly one target's lock/client/state lifecycle and one state commit."""

    def __init__(
        self,
        *,
        client_loader: ClientLoader,
        notifier: NotificationService,
        reporter: RunReporter,
        now_fn: Callable[[], datetime.datetime],
        executor_type: type[ItemExecutor] | None = None,
        acquire_lock_fn: Callable[[str], AbstractContextManager[Any]] = acquire_lock,
        save_traceback_fn: Callable[..., None] = save_traceback,
    ) -> None:
        self.client_loader = client_loader
        self.notifier = notifier
        self.reporter = reporter
        self.now_fn = now_fn
        self.executor_type = executor_type or ItemExecutor
        self.acquire_lock_fn = acquire_lock_fn
        self.save_traceback_fn = save_traceback_fn

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
            try:
                self.save_traceback_fn(logger, target_name=target, log_to_console=False)
            except Exception:
                pass
            return False

    def run(
        self,
        load: TargetLoad,
        logger: logging.Logger,
        interrupted: Callable[[], bool],
    ) -> TargetRunOutcome:
        plugin = load.plugin
        result = TargetRunOutcome()
        failed_items: list[tuple[TrackedItem, Exception]] = []
        executor: ItemExecutor | None = None
        try:
            with self.acquire_lock_fn(plugin.target):
                assert load.state is not None
                client = None
                primary_error: Exception | None = None
                cleanup_error: Exception | None = None
                try:
                    client = self.client_loader.load(plugin, load.settings)
                    executor = self.executor_type(
                        target=plugin.target,
                        display_name=plugin.display_name,
                        client=client,
                        state=load.state,
                        notifier=self.notifier,
                        reporter=self.reporter,
                        logger=logger,
                        interrupted=interrupted,
                        now_fn=self.now_fn,
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
                        result.rate_limited |= item_outcome.rate_limited
                        result.scrape_error |= item_outcome.affects_scrape_status
                        result.notification_error |= item_outcome.notification_failed
                    if load.state.has_pending:
                        try:
                            load.state.save()
                        except StorageFileError as exc:
                            self.reporter.log_error(
                                "Storage",
                                messages.state_save_failed(plugin.target),
                                str(exc),
                            )
                            result.storage_error = True
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
                    lambda: self.notifier.notify_old_entries(
                        plugin.display_name,
                        stale_items,
                        OLD_ENTRY_HOURS,
                        plugin.item_reference_url,
                    ),
                    logger=logger,
                    target=plugin.target,
                )
                if not delivered:
                    result.notification_error = True
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
                        result.notification_error = True
                        self.reporter.log_warning(
                            "Notifications", messages.WARN_ERROR_NOTIFICATION_FAILED
                        )
        except LockAcquisitionError:
            self.reporter.log_error("System", messages.ERR_LOCK_HELD)
            result.skipped = True
        except PluginDependencyError as exc:
            self.reporter.log_error("System", str(exc))
            result.dependency_error = True
        except Exception as exc:
            try:
                self.save_traceback_fn(logger, target_name=plugin.target, log_to_console=False)
            except Exception:
                pass
            self.reporter.log_error(
                "Scraper",
                messages.plugin_lifecycle_failed(type(exc).__name__),
                messages.errors_log_pointer(plugin.target),
            )
            result.scrape_error = True
        return result


__all__ = ["TargetRunner", "TargetRunOutcome"]
