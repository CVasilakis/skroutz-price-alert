import contextlib
from datetime import datetime, timezone
from unittest import mock

import pytest

from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
)
from core.exceptions import LockAcquisitionError, PluginDependencyError, StateFileError
from core.orchestrator import ScrapingOrchestrator
from core.preflight import TargetLoad
from core.run import ItemRunOutcome, RunReporter
from core.scrapers.api import ScraperPlugin, TrackedItem
from core.scrapers.registry import ClientFactory, compile_plugin
from core.scrapers.state import JsonStateRepository
from core.settings import resolve_settings

NOW = datetime(2026, 7, 18, tzinfo=timezone.utc)


def _plugin():
    return compile_plugin(
        ScraperPlugin(
            display_name="Store",
            domains=["store.example"],
            accepts_url=lambda _url: True,
        ),
        target="store",
        package="tests.store",
    )


def _load(*, items=(), state=None, error=None, state_error=False):
    plugin = _plugin()
    return TargetLoad(
        plugin,
        resolve_settings(plugin.setting_specs, {}),
        tuple(items),
        (),
        state,
        error,
        state_error,
    )


def _orchestrator(load):
    factory = mock.create_autospec(ClientFactory, instance=True)
    notifier = mock.Mock(has_services=False)
    reporter = mock.create_autospec(RunReporter, instance=True)
    orchestrator = ScrapingOrchestrator(
        [load], factory, notifier, reporter=reporter, now_fn=lambda: NOW,
    )
    return orchestrator, factory, notifier, reporter


@pytest.fixture(autouse=True)
def _runtime_seams(monkeypatch):
    monkeypatch.setattr("core.orchestrator.signal.signal", mock.Mock())
    monkeypatch.setattr("core.orchestrator.get_target_logger", mock.Mock(return_value=mock.Mock()))


def test_config_and_state_failures_keep_distinct_exit_status_and_reporting():
    config_run, _, _, config_reporter = _orchestrator(_load(error="bad config"))
    assert config_run.run() == EXIT_CODE_PRODUCTS_ERROR
    assert config_reporter.start_target.call_args.args[3].error == "bad config"

    item = TrackedItem("one", "One", "https://store.example/one", 1)
    state_run, _, _, state_reporter = _orchestrator(
        _load(items=[item], error="bad state", state_error=True)
    )
    assert state_run.run() == EXIT_CODE_STORAGE_ERROR
    assert state_reporter.start_target.call_args.args[3].loaded_count == 1
    assert state_reporter.start_target.call_args.args[3].error is None
    state_reporter.log_error.assert_called_once()


def test_lock_and_dependency_failures_are_isolated(monkeypatch):
    item = TrackedItem("one", "One", "https://store.example/one", 1)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    lock_run, _, _, lock_reporter = _orchestrator(_load(items=[item], state=state))
    monkeypatch.setattr("core.orchestrator.acquire_lock", mock.Mock(side_effect=LockAcquisitionError))
    assert lock_run.run() == EXIT_CODE_SKIPPED
    lock_reporter.log_error.assert_called_once()

    dependency_run, factory, _, dependency_reporter = _orchestrator(
        _load(items=[item], state=state)
    )
    monkeypatch.setattr("core.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext())
    factory.create.side_effect = PluginDependencyError("install it")
    assert dependency_run.run() == EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
    dependency_reporter.log_error.assert_called_once()


def test_one_state_commit_failure_is_storage_error(monkeypatch):
    item = TrackedItem("one", "One", "https://store.example/one", 1)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.has_pending = True
    state.save.side_effect = StateFileError("disk full")
    run, _, _, reporter = _orchestrator(_load(items=[item], state=state))
    monkeypatch.setattr("core.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext())
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(item)
    executor_type.return_value.stale_items = []
    monkeypatch.setattr("core.orchestrator.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_STORAGE_ERROR
    state.save.assert_called_once()
    assert "state/store.json" in reporter.log_error.call_args.args[1]


def test_success_commits_once_and_aggregates_notification_failures(monkeypatch):
    item = TrackedItem("one", "One", "https://store.example/one", 1)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.has_pending = True
    run, _, notifier, reporter = _orchestrator(_load(items=[item], state=state))
    notifier.has_services = True
    notifier.notify_old_entries.return_value = False
    notifier.notify_errors.return_value = False
    monkeypatch.setattr("core.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext())
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(
        item, reported_error=RuntimeError("broken")
    )
    executor_type.return_value.stale_items = [item]
    monkeypatch.setattr("core.orchestrator.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_NOTIFICATION_ERROR
    state.save.assert_called_once()
    notifier.notify_old_entries.assert_called_once_with("Store", [item], 48)
    notifier.notify_errors.assert_called_once()
    assert reporter.log_warning.call_count == 2


def test_interruption_stops_target_and_wins_exit_priority(monkeypatch):
    item = TrackedItem("one", "One", "https://store.example/one", 1)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    run, _, _, reporter = _orchestrator(_load(items=[item], state=state))
    run._interrupt_message = "Received signal SIGTERM"
    monkeypatch.setattr("core.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext())
    executor_type = mock.MagicMock()

    def interrupt(_item):
        run.interrupted = True
        return ItemRunOutcome(item, affects_scrape_status=True)

    executor_type.return_value.process.side_effect = interrupt
    executor_type.return_value.stale_items = []
    monkeypatch.setattr("core.orchestrator.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_INTERRUPT
    reporter.log_interrupt.assert_called_once_with("Received signal SIGTERM")
