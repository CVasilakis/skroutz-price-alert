import contextlib
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from core.application.contracts import ItemRunOutcome, RunReporter
from core.application.orchestrator import ScrapingOrchestrator
from core.application.preflight import LoadFailure, LoadFailureKind, TargetLoad
from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
)
from core.exceptions import LockAcquisitionError, PluginDependencyError, StateFileError
import core.infrastructure.logging
from core.scrapers.api import ScraperPlugin, TrackedItem, UrlField
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.compiler import compile_plugin
from core.scrapers.framework.settings import KEY_SUPPRESS_REPEATED_PRICE_ALERTS
from core.scrapers.framework.state import JsonStateRepository
from core.settings import resolve_settings

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)
URL = UrlField("url", domains=("store.example",), accepts_url=lambda _url: True)


def _plugin():
    return compile_plugin(
        ScraperPlugin(
            display_name="Store",
            item_fields=(URL,),
            reference_url=URL,
        ),
        target="store",
        package="tests.store",
    )


def _load(*, items=(), state=None, error=None, failure_kind=LoadFailureKind.CONFIG, settings=None):
    plugin = _plugin()
    failure = None
    if error is not None:
        failure = LoadFailure(failure_kind, error)
    return TargetLoad(
        plugin=plugin,
        settings=resolve_settings(plugin.setting_specs, settings or {}),
        items=tuple(items),
        state=state,
        failure=failure,
    )


def _orchestrator(load):
    loader = mock.create_autospec(ClientLoader, instance=True)
    notifier = mock.Mock(has_services=False)
    reporter = mock.create_autospec(RunReporter, instance=True)
    orchestrator = ScrapingOrchestrator(
        [load],
        loader,
        notifier,
        reporter=reporter,
        now_fn=lambda: NOW,
    )
    return orchestrator, loader, notifier, reporter


@pytest.fixture(autouse=True)
def _runtime_seams(monkeypatch):
    monkeypatch.setattr("core.application.orchestrator.signal.signal", mock.Mock())
    monkeypatch.setattr(
        "core.application.orchestrator.get_target_logger", mock.Mock(return_value=mock.Mock())
    )


def test_config_and_state_failures_keep_distinct_exit_status_and_reporting():
    config_run, _, _, config_reporter = _orchestrator(
        _load(error="bad config")
    )
    assert config_run.run() == EXIT_CODE_PRODUCTS_ERROR
    assert config_reporter.start_target.call_args.args[3].error == "bad config"

    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state_run, _, _, state_reporter = _orchestrator(
        _load(items=[item], error="bad state", failure_kind=LoadFailureKind.STATE)
    )
    assert state_run.run() == EXIT_CODE_STORAGE_ERROR
    assert state_reporter.start_target.call_args.args[3].loaded_count == 1
    assert state_reporter.start_target.call_args.args[3].error is None
    state_reporter.log_error.assert_called_once()


def test_load_diagnostic_is_logged_before_the_target_is_reported():
    load = _load(error="bad config")
    load = TargetLoad(
        plugin=load.plugin,
        settings=load.settings,
        failure=LoadFailure(
            LoadFailureKind.CONFIG,
            "Fix `config/store.json`.",
            "Path: /absolute/config/store.json\nException: PermissionError\nErrno: 13",
        ),
    )
    run, _, _, reporter = _orchestrator(load)

    assert run.run() == EXIT_CODE_PRODUCTS_ERROR

    content = (
        Path(core.infrastructure.logging.LOGS_DIR) / "store" / "errors.txt"
    ).read_text()
    assert "Path: /absolute/config/store.json" in content
    assert "Errno: 13" in content
    assert reporter.start_target.called


def test_lock_and_dependency_failures_are_isolated(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    lock_run, _, _, lock_reporter = _orchestrator(_load(items=[item], state=state))
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", mock.Mock(side_effect=LockAcquisitionError)
    )
    assert lock_run.run() == EXIT_CODE_SKIPPED
    lock_reporter.log_error.assert_called_once()

    dependency_run, loader, _, dependency_reporter = _orchestrator(_load(items=[item], state=state))
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    loader.load.side_effect = PluginDependencyError("install it")
    assert dependency_run.run() == EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
    dependency_reporter.log_error.assert_called_once()


def test_one_state_commit_failure_is_storage_error(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.has_pending = True
    state.save.side_effect = StateFileError(
        "Cannot save `state/store.json`; check the error log.",
        "Path: /project/state/store.json\nException: OSError\nDetail: disk full",
    )
    run, _, _, reporter = _orchestrator(
        _load(
            items=[item],
            state=state,
            settings={KEY_SUPPRESS_REPEATED_PRICE_ALERTS: True},
        )
    )
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(item)
    executor_type.return_value.stale_items = []
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_STORAGE_ERROR
    state.save.assert_called_once()
    assert executor_type.call_args.kwargs["suppress_repeated_price_alerts"] is True
    assert reporter.log_error.call_args.args[1] == "Latest scrape state was not saved."
    assert reporter.log_error.call_args.args[2] == (
        "Cannot save `state/store.json`; check the error log."
    )
    diagnostic_log = (
        Path(core.infrastructure.logging.LOGS_DIR) / "store" / "errors.txt"
    ).read_text()
    assert "Path: /project/state/store.json" in diagnostic_log
    assert "Detail: disk full" in diagnostic_log


def test_success_commits_once_and_aggregates_notification_failures(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.has_pending = True
    run, loader, notifier, reporter = _orchestrator(_load(items=[item], state=state))
    notifier.has_services = True
    notifier.notify_old_entries.return_value = False
    notifier.notify_errors.return_value = False
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(
        item, reported_error=RuntimeError("broken")
    )
    executor_type.return_value.stale_items = [item]
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_NOTIFICATION_ERROR
    state.save.assert_called_once()
    notifier.notify_old_entries.assert_called_once_with("Store", [item], 48, mock.ANY)
    notifier.notify_errors.assert_called_once()
    assert reporter.log_warning.call_count == 2
    loader.load.return_value.close.assert_called_once()


def test_interruption_stops_target_and_wins_exit_priority(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    run, loader, _, reporter = _orchestrator(_load(items=[item], state=state))
    run._interrupt_message = "Received signal SIGTERM"
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    executor_type = mock.MagicMock()

    def interrupt(_item):
        run.interrupted = True
        return ItemRunOutcome(item, affects_scrape_status=True)

    executor_type.return_value.process.side_effect = interrupt
    executor_type.return_value.stale_items = []
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_INTERRUPT
    reporter.log_interrupt.assert_called_once_with("Received signal SIGTERM")
    loader.load.return_value.close.assert_called_once()


def test_client_closes_and_target_failure_is_isolated(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    run, loader, _, _ = _orchestrator(_load(items=[item], state=state))
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "core.application.target.ItemExecutor", mock.Mock(side_effect=RuntimeError("broken"))
    )

    assert run.run() == EXIT_CODE_SCRAPE_ERROR
    loader.load.return_value.close.assert_called_once()


def test_cleanup_fault_does_not_hide_primary_target_fault(monkeypatch):
    class PrimaryFault(Exception):
        pass

    class CleanupFault(Exception):
        pass

    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = mock.create_autospec(JsonStateRepository, instance=True)
    run, loader, _, reporter = _orchestrator(_load(items=[item], state=state))
    loader.load.return_value.close.side_effect = CleanupFault("close")
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "core.application.target.ItemExecutor", mock.Mock(side_effect=PrimaryFault("execute"))
    )

    assert run.run() == EXIT_CODE_SCRAPE_ERROR
    assert "PrimaryFault" in reporter.log_error.call_args.args[1]


def test_startup_fault_does_not_prevent_later_target(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    first_state = mock.create_autospec(JsonStateRepository, instance=True)
    second_state = mock.create_autospec(JsonStateRepository, instance=True)
    first, second = _load(items=[item], state=first_state), _load(items=[item], state=second_state)
    loader = mock.create_autospec(ClientLoader, instance=True)
    healthy_client = mock.Mock()
    loader.load.side_effect = [RuntimeError("constructor fault"), healthy_client]
    reporter = mock.create_autospec(RunReporter, instance=True)
    run = ScrapingOrchestrator(
        [first, second],
        loader,
        mock.Mock(has_services=False),
        reporter=reporter,
        now_fn=lambda: NOW,
    )
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(item)
    executor_type.return_value.stale_items = []
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_SCRAPE_ERROR
    assert loader.load.call_count == 2
    healthy_client.close.assert_called_once()
    assert reporter.complete_target.call_count == 2
