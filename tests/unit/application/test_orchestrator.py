import contextlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

import core.infrastructure.logging
from core.application.contracts import ItemRunOutcome, RunReporter
from core.application.orchestrator import ScrapingOrchestrator
from core.application.preflight import TargetConfigFailure, TargetConfigLoad
from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_TARGET_CONFIG_ERROR,
)
from core.exceptions import LockAcquisitionError, PluginDependencyError, StateFileError
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
        ScraperPlugin(display_name="Store", item_fields=(URL,), reference_url=URL),
        target="store",
        package="tests.store",
    )


def _load(*, items=(), error=None, diagnostic=None, settings=None):
    plugin = _plugin()
    return TargetConfigLoad(
        plugin=plugin,
        settings=resolve_settings(plugin.setting_specs, settings or {}),
        items=tuple(items),
        failure=TargetConfigFailure(error, diagnostic) if error is not None else None,
    )


def _state_mock():
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.has_pending = False
    return state


def _orchestrator(load, *, state=None, state_factory=None):
    loader = mock.create_autospec(ClientLoader, instance=True)
    notifier = mock.Mock(has_services=False)
    reporter = mock.create_autospec(RunReporter, instance=True)
    state = state or _state_mock()
    factory = state_factory or mock.Mock(return_value=state)
    orchestrator = ScrapingOrchestrator(
        [load],
        loader,
        notifier,
        reporter=reporter,
        now_fn=lambda: NOW,
        state_dir="/project/state",
        state_repository_factory=factory,
    )
    return orchestrator, loader, notifier, reporter, factory, state


@pytest.fixture(autouse=True)
def _runtime_seams(monkeypatch):
    monkeypatch.setattr("core.application.orchestrator.signal.signal", mock.Mock())
    monkeypatch.setattr(
        "core.application.orchestrator.get_target_logger", mock.Mock(return_value=mock.Mock())
    )


def test_config_and_state_failures_keep_distinct_exit_status_and_reporting(monkeypatch):
    config_run, _, _, config_reporter, factory, _ = _orchestrator(_load(error="bad config"))
    assert config_run.run() == EXIT_CODE_TARGET_CONFIG_ERROR
    assert config_reporter.start_target.call_args.args[3].error == "bad config"
    factory.assert_not_called()

    state = _state_mock()
    state.load.side_effect = StateFileError("bad state", "technical state detail")
    state_run, loader, _, state_reporter, _, _ = _orchestrator(_load(), state=state)
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    assert state_run.run() == EXIT_CODE_STORAGE_ERROR
    assert state_reporter.start_target.call_args.args[3].error is None
    state_reporter.log_storage_error.assert_called_once_with(
        "Scrape state could not be loaded.",
        "bad state",
    )
    state_reporter.log_error.assert_not_called()
    loader.load.assert_not_called()


def test_load_diagnostic_is_logged_before_the_target_is_reported():
    run, _, _, reporter, _, _ = _orchestrator(
        _load(
            error="Fix `config/store.json`.",
            diagnostic=("Path: /absolute/config/store.json\nException: PermissionError\nErrno: 13"),
        )
    )

    assert run.run() == EXIT_CODE_TARGET_CONFIG_ERROR
    content = (Path(core.infrastructure.logging.LOGS_DIR) / "store" / "errors.txt").read_text()
    assert "Path: /absolute/config/store.json" in content
    assert "Errno: 13" in content
    assert reporter.start_target.called


def test_lock_failure_prevents_state_and_client_access(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    run, loader, _, reporter, factory, _ = _orchestrator(_load(items=[item]))
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", mock.Mock(side_effect=LockAcquisitionError)
    )

    assert run.run() == EXIT_CODE_SKIPPED
    factory.assert_not_called()
    loader.load.assert_not_called()
    reporter.log_system_error.assert_called_once_with(
        "Another instance is currently running. Aborting..."
    )
    reporter.log_error.assert_not_called()


def test_dependency_failure_happens_after_locked_state_load(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    run, loader, _, reporter, _, state = _orchestrator(_load(items=[item]))
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    loader.load.side_effect = PluginDependencyError("install it")

    assert run.run() == EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
    state.load.assert_called_once()
    reporter.log_system_error.assert_called_once_with("install it")
    reporter.log_error.assert_not_called()


def test_zero_items_still_loads_state_and_malformed_state_is_storage_error(monkeypatch):
    state = _state_mock()
    state.load.side_effect = StateFileError("malformed")
    run, loader, _, _, factory, _ = _orchestrator(_load(), state=state)
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )

    assert run.run() == EXIT_CODE_STORAGE_ERROR
    factory.assert_called_once()
    state.load.assert_called_once()
    state.save.assert_not_called()
    loader.load.assert_not_called()


def test_state_load_failure_is_isolated_from_later_targets(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    first_state = _state_mock()
    first_state.load.side_effect = StateFileError("malformed")
    second_state = _state_mock()
    factory = mock.Mock(side_effect=(first_state, second_state))
    loader = mock.create_autospec(ClientLoader, instance=True)
    reporter = mock.create_autospec(RunReporter, instance=True)
    run = ScrapingOrchestrator(
        [_load(items=[item]), _load(items=[item])],
        loader,
        mock.Mock(has_services=False),
        reporter=reporter,
        state_dir="/project/state",
        state_repository_factory=factory,
    )
    executor_type = mock.MagicMock()
    executor_type.return_value.process.return_value = ItemRunOutcome(item)
    executor_type.return_value.stale_items = []
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == EXIT_CODE_STORAGE_ERROR
    assert factory.call_count == 2
    loader.load.assert_called_once()
    loader.load.return_value.close.assert_called_once()
    assert reporter.complete_target.call_count == 2


def test_lock_entry_precedes_repository_construction_and_load(monkeypatch):
    events = []
    state = _state_mock()
    state.load.side_effect = lambda: events.append("load")

    @contextlib.contextmanager
    def locked(_target):
        events.append("lock")
        yield

    factory = mock.Mock(side_effect=lambda *args, **kwargs: (events.append("construct"), state)[1])
    run, _, _, _, _, _ = _orchestrator(_load(), state_factory=factory)
    monkeypatch.setattr("core.application.orchestrator.acquire_lock", locked)

    assert run.run() == 0
    assert events == ["lock", "construct", "load"]


def test_state_change_before_lock_entry_is_observed(tmp_path, monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state_path = tmp_path / "state" / "store.json"

    @contextlib.contextmanager
    def locked(_target):
        state_path.parent.mkdir()
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "items": {
                        "one": {
                            "last_price": 7,
                            "last_checked": "2026-07-17T00:00:00Z",
                        }
                    },
                }
            )
        )
        yield

    executor_type = mock.MagicMock()

    def capture_state(**kwargs):
        assert kwargs["state"].get("one").last_price == 7
        executor = mock.Mock()
        executor.process.return_value = ItemRunOutcome(item)
        executor.stale_items = []
        return executor

    executor_type.side_effect = capture_state
    run, _, _, _, _, _ = _orchestrator(
        _load(items=[item]),
        state_factory=JsonStateRepository,
    )
    run.state_dir = str(tmp_path / "state")
    monkeypatch.setattr("core.application.orchestrator.acquire_lock", locked)
    monkeypatch.setattr("core.application.target.ItemExecutor", executor_type)

    assert run.run() == 0


def test_one_state_commit_failure_is_storage_error(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = _state_mock()
    state.has_pending = True
    state.save.side_effect = StateFileError(
        "Cannot save `state/store.json`; check the error log.",
        "Path: /project/state/store.json\nException: OSError\nDetail: disk full",
    )
    run, _, _, reporter, _, _ = _orchestrator(
        _load(
            items=[item],
            settings={KEY_SUPPRESS_REPEATED_PRICE_ALERTS: True},
        ),
        state=state,
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
    reporter.log_storage_error.assert_called_once_with(
        "Latest scrape state was not saved.",
        "Cannot save `state/store.json`; check the error log.",
    )
    reporter.log_error.assert_not_called()


def test_success_commits_once_closes_client_and_aggregates_notification_failures(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    state = _state_mock()
    state.has_pending = True
    run, loader, notifier, reporter, _, _ = _orchestrator(_load(items=[item]), state=state)
    notifier.has_services = True
    notifier.notify_stale_items.return_value = False
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
    state.load.assert_called_once()
    state.save.assert_called_once()
    notifier.notify_stale_items.assert_called_once_with("Store", [item], 48, mock.ANY)
    notifier.notify_errors.assert_called_once()
    assert reporter.log_warning.call_count == 2
    loader.load.return_value.close.assert_called_once()


def test_interruption_stops_target_and_wins_exit_priority(monkeypatch):
    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    run, loader, _, reporter, _, _ = _orchestrator(_load(items=[item]))
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


def test_cleanup_fault_does_not_hide_primary_target_fault(monkeypatch):
    class PrimaryFault(Exception):
        pass

    class CleanupFault(Exception):
        pass

    item = TrackedItem("one", "One", 1, _custom={URL: "https://store.example/one"})
    run, loader, _, reporter, _, _ = _orchestrator(_load(items=[item]))
    loader.load.return_value.close.side_effect = CleanupFault("close")
    monkeypatch.setattr(
        "core.application.orchestrator.acquire_lock", lambda _target: contextlib.nullcontext()
    )
    monkeypatch.setattr(
        "core.application.target.ItemExecutor", mock.Mock(side_effect=PrimaryFault("execute"))
    )

    assert run.run() == EXIT_CODE_SCRAPE_ERROR
    assert "PrimaryFault" in reporter.log_error.call_args.args[1]
    loader.load.return_value.close.assert_called_once()
