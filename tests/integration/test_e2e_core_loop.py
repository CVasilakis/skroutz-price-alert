"""End-to-end tests of the core execution loop against a real local HTTP server.

These drive the full chain — config file on disk -> preflight ``load_targets`` ->
real ``ScrapingOrchestrator`` -> real registry -> a urllib scraper client hitting
``http.server`` -> real JSON storage write-back — with only two surgical patches
(the pacing sleep and the signal-handler install). The fake store lives in
``tests/integration/fake_store.py`` and is registered through the shared ``registry_sandbox``,
so no real plugin (or its dependencies) is involved; the whole suite runs on the
stdlib and finishes in about a second.

The UI strategy is the autospec'd ``ExecutionStrategy`` double so each case can
assert the exact rendered payload (``core.messages``); one final test goes through
``core.main.main()`` itself with the real ``SilentExecutionStrategy`` to prove the
argparse -> preflight -> reminder -> orchestrator wiring end to end.
"""

import datetime
import json
import os
import sys
from unittest import mock

import pytest

from core import messages
from core import orchestrator as orchestrator_module
from core.constants import (
    MAX_RETRIES, TIMESTAMP_FORMAT,
    EXIT_CODE_SUCCESS, EXIT_CODE_RATE_LIMIT_ERROR, EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_SKIPPED, EXIT_CODE_ERROR, EXIT_CODE_ENV_ERROR,
)
from core.locks import acquire_lock
from core.orchestrator import ScrapingOrchestrator
from core.scrapers.registry import ScraperRegistry
from core.preflight import load_targets
from core.ui.tui import PriceOutcome

from integration.fake_store import FakeStoreClient, FakeStoreDataManager, fake_store_server
from support import fake_plugin, mock_notifier, mock_ui, registry_sandbox


def _fakestore_plugin(netloc):
    """The fake store declares a host only; request URLs retain the random port."""
    return fake_plugin(name="fakestore", domains=(netloc.rsplit(":", 1)[0],), config="fakestore.json",
                       client_class=FakeStoreClient, storage_class=FakeStoreDataManager)


def _write_config(cfg_dir, products, filename="fakestore.json"):
    path = os.path.join(str(cfg_dir), filename)
    with open(path, "w") as f:
        json.dump({"products": products}, f)
    return path


def _run_orchestrator(cfg_dir, notifier, ui, targets=("fakestore",)):
    """Runs the real orchestrator over the given targets with real components.

    Only the pacing sleep and the signal-handler install are patched; everything
    else (registry, settings resolution, storage, client, locks) is production code.
    """
    targets = list(targets)
    registry = ScraperRegistry(str(cfg_dir))
    loads_by_target = {tl.target: tl for tl in load_targets(registry, targets)}
    orch = ScrapingOrchestrator(
        targets_to_run=targets, registry=registry, notifier=notifier,
        config_dir=str(cfg_dir), quiet=True, ui_strategy=ui,
        loads_by_target=loads_by_target,
    )
    with mock.patch.object(orch, "_sleep_with_jitter"), \
         mock.patch.object(orchestrator_module.signal, "signal"):
        return orch.run()


def test_price_drop_notifies_and_writes_back(tmp_path):
    routes = {"/drop/1": [(200, {"price": 79.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            url = f"http://{netloc}/drop/1"
            config_path = _write_config(tmp_path, [
                {"name": "Widget", "url": url, "target_price": 100.0},
            ])
            notifier, ui = mock_notifier(has_services=True, delivery_ok=True), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    notifier.notify_low_price.assert_called_once_with("Widget", 100.0, 79.0, url, "€")
    ui.log_price_result.assert_called_once_with(
        "Widget", 79.0, "€", 100.0, PriceOutcome.DROP,
        notes=[messages.NOTE_NOTIFIED_OK], attempt_notes=[])
    # The scraped price and check timestamp were persisted to the config on disk.
    with open(config_path) as f:
        row = json.load(f)["products"][0]
    assert row["last_price"] == 79.0
    assert row["last_checked"]
    # The per-target lock was released: it can be re-acquired immediately.
    with acquire_lock("fakestore"):
        pass


def test_mixed_non_object_row_is_reported_preserved_and_does_not_block_valid_row(tmp_path):
    routes = {"/drop/1": [(200, {"price": 79.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            url = f"http://{netloc}/drop/1"
            config_path = _write_config(tmp_path, [
                None,
                {"name": "Widget", "url": url, "target_price": 100.0},
            ])
            notifier, ui = mock_notifier(has_services=True), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    config_arg = ui.start_target.call_args.args[4]
    assert config_arg.value == "2 loaded, [yellow]1 misconfigured[/yellow]"
    assert "JSON index: 1" in config_arg.footnote
    notifier.notify_low_price.assert_called_once()
    with open(config_path) as file:
        rows = json.load(file)["products"]
    assert rows[0] is None
    assert rows[1]["last_price"] == 79.0


def test_malformed_url_null_name_and_numeric_timestamp_are_contained(tmp_path):
    plugin = fake_plugin(
        name="fakestore", domains=("fake-store.example",), config="fakestore.json",
        client_class=FakeStoreClient, storage_class=FakeStoreDataManager,
    )
    with registry_sandbox(plugin):
        config_path = _write_config(tmp_path, [
            {"name": None, "url": "https://[", "target_price": 10,
             "last_checked": 123},
        ])
        notifier, ui = mock_notifier(), mock_ui()

        exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    ui.log_error.assert_called_once_with(
        "Unknown", messages.WARN_INVALID_URL, notes=messages.NOTE_CORRUPTED_TIMESTAMP)
    with open(config_path) as file:
        row = json.load(file)["products"][0]
    assert row["last_checked"] != 123


def test_product_gone_is_a_red_row_without_failing_the_run(tmp_path):
    routes = {"/gone/1": [(404, None)]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            _write_config(tmp_path, [
                {"name": "Removed", "url": f"http://{netloc}/gone/1", "target_price": 50.0},
            ])
            notifier, ui = mock_notifier(has_services=True), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    ui.log_error.assert_called_once_with(
        "Removed", messages.skipping_warning("ProductNotFoundError"),
        notes=[messages.not_found_detail(404)], attempt_notes=[])
    notifier.notify_low_price.assert_not_called()
    notifier.notify_errors.assert_not_called()


def test_server_error_retries_then_succeeds(tmp_path):
    routes = {"/flaky/1": [(503, None), (200, {"price": 120.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            _write_config(tmp_path, [
                {"name": "Flaky", "url": f"http://{netloc}/flaky/1", "target_price": 100.0},
            ])
            notifier, ui = mock_notifier(), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    ui.log_price_result.assert_called_once_with(
        "Flaky", 120.0, "€", 100.0, PriceOutcome.OK,
        notes=[messages.succeeded_on_attempt(2, MAX_RETRIES)],
        attempt_notes=[messages.attempt_note(1, "ServerError")])


def test_rate_limited_on_every_attempt_exits_17(tmp_path):
    routes = {"/blocked/1": [(429, None)]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            _write_config(tmp_path, [
                {"name": "Blocked", "url": f"http://{netloc}/blocked/1", "target_price": 50.0},
            ])
            notifier, ui = mock_notifier(), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_RATE_LIMIT_ERROR
    ui.log_failure.assert_called_once_with(
        "Blocked", "RateLimitError",
        attempt_notes=[messages.attempt_note(i, "RateLimitError")
                       for i in range(1, MAX_RETRIES + 1)],
        extra_notes=[messages.NOTE_RATE_LIMIT_ABORTED,
                     messages.errors_log_pointer("fakestore")])
    notifier.notify_errors.assert_not_called()


def test_invalid_config_json_skips_target_and_exits_15(tmp_path):
    # No server needed: the target never gets past its products-config load.
    with registry_sandbox(_fakestore_plugin("127.0.0.1:9")):
        with open(os.path.join(str(tmp_path), "fakestore.json"), "w") as f:
            f.write("{ not json")
        notifier, ui = mock_notifier(), mock_ui()

        exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_PRODUCTS_ERROR
    # The failed Config row opened (and closed) the target's panel; nothing scraped.
    ui.start_target.assert_called_once()
    ui.complete_target.assert_called_once()
    ui.log_price_result.assert_not_called()


def test_lock_held_by_another_instance_exits_42(tmp_path):
    """A concurrently held per-target lock skips the target: the real contention
    path (real ``FileLock``, ``LOCK_TIMEOUT=0``), not a hand-scripted panel."""
    # No server needed: the lock is acquired before any scraping starts.
    with registry_sandbox(_fakestore_plugin("127.0.0.1:9")):
        _write_config(tmp_path, [
            {"name": "Widget", "url": "http://127.0.0.1:9/p/1", "target_price": 50.0},
        ])
        notifier, ui = mock_notifier(), mock_ui()

        with acquire_lock("fakestore"):  # the "other instance"
            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    # Every (i.e. the only) target was skipped, so the run reports exit 42.
    assert exit_code == EXIT_CODE_SKIPPED
    ui.log_error.assert_called_once_with("System", messages.ERR_LOCK_HELD)
    ui.log_price_result.assert_not_called()
    notifier.notify_errors.assert_not_called()


def _broken_plugin():
    """A second registered store whose products config will fail to load."""
    return fake_plugin(name="brokenstore", domains=("broken.example",),
                       config="brokenstore.json",
                       client_class=FakeStoreClient, storage_class=FakeStoreDataManager)


def test_multi_target_broken_config_does_not_stop_healthy_target(tmp_path):
    """Per-target isolation + the exit ladder: the broken store is skipped with
    exit 15, while the healthy store still scrapes and writes back."""
    routes = {"/drop/1": [(200, {"price": 79.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_broken_plugin(), _fakestore_plugin(netloc)):
            url = f"http://{netloc}/drop/1"
            healthy_path = _write_config(tmp_path, [
                {"name": "Widget", "url": url, "target_price": 100.0},
            ])
            with open(os.path.join(str(tmp_path), "brokenstore.json"), "w") as f:
                f.write("{ not json")
            notifier, ui = mock_notifier(has_services=True, delivery_ok=True), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui,
                                          targets=["brokenstore", "fakestore"])

    # The persistent setup problem decides the exit code...
    assert exit_code == EXIT_CODE_PRODUCTS_ERROR
    # ...but the healthy target completed its full pass regardless.
    notifier.notify_low_price.assert_called_once_with("Widget", 100.0, 79.0, url, "€")
    with open(healthy_path) as f:
        row = json.load(f)["products"][0]
    assert row["last_price"] == 79.0
    assert row["last_checked"]


def test_multi_target_rate_limit_does_not_stop_healthy_target(tmp_path):
    """A rate-limit abort is scoped to its own store: the second target still
    runs, and the run exits 17."""
    routes = {
        "/blocked/1": [(429, None)],
        "/ok/1": [(200, {"price": 150.0, "currency": "€"})],
    }
    with fake_store_server(routes) as netloc:
        # Two plugins on the same live server: domain overlap is rejected by the
        # registry, so the second store routes through a distinct loopback name.
        with fake_store_server(routes) as netloc2:
            limited_port = netloc2.rsplit(":", 1)[1]
            limited = fake_plugin(name="limitedstore", domains=("localhost",),
                                  config="limitedstore.json",
                                  client_class=FakeStoreClient,
                                  storage_class=FakeStoreDataManager)
            with registry_sandbox(limited, _fakestore_plugin(netloc)):
                _write_config(tmp_path, [
                    {"name": "Blocked", "url": f"http://localhost:{limited_port}/blocked/1", "target_price": 50.0},
                ], filename="limitedstore.json")
                healthy_path = _write_config(tmp_path, [
                    {"name": "Steady", "url": f"http://{netloc}/ok/1", "target_price": 100.0},
                ])
                notifier, ui = mock_notifier(), mock_ui()

                exit_code = _run_orchestrator(tmp_path, notifier, ui,
                                              targets=["limitedstore", "fakestore"])

    assert exit_code == EXIT_CODE_RATE_LIMIT_ERROR
    # The healthy target still scraped and persisted its result.
    ui.log_price_result.assert_called_once_with(
        "Steady", 150.0, "€", 100.0, PriceOutcome.OK, notes=[], attempt_notes=[])
    with open(healthy_path) as f:
        row = json.load(f)["products"][0]
    assert row["last_price"] == 150.0


def test_main_happy_path_through_real_wiring(tmp_path):
    """One test through core.main.main() itself: argparse -> preflight ->
    reminder -> orchestrator against the fake store, with the real
    SilentExecutionStrategy. Patched seams: CONFIG_DIR, the .env check, the
    pacing sleep, and the signal-handler install — nothing else."""
    import core.main

    routes = {"/drop/1": [(200, {"price": 79.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            url = f"http://{netloc}/drop/1"
            config_path = _write_config(tmp_path, [
                {"name": "Widget", "url": url, "target_price": 100.0},
            ])
            with mock.patch.object(sys, "argv", ["main", "--quiet", "--fakestore"]), \
                 mock.patch("core.main.CONFIG_DIR", str(tmp_path)), \
                 mock.patch("core.ui.config_check.check_env_file"), \
                 mock.patch.dict(os.environ, {"NOTIFICATION_URLS": ""}), \
                 mock.patch.object(ScrapingOrchestrator, "_sleep_with_jitter"), \
                 mock.patch.object(orchestrator_module.signal, "signal"):
                with pytest.raises(SystemExit) as caught:
                    core.main.main()

    assert caught.value.code == EXIT_CODE_SUCCESS
    with open(config_path) as f:
        row = json.load(f)["products"][0]
    assert row["last_price"] == 79.0
    assert row["last_checked"]


def test_reminder_delivers_through_main(tmp_path):
    """The full reminder chain through the real entry point: a due slot is read
    from general.json, the reminder is sent once, and the advanced slot is
    persisted — before the orchestrator runs."""
    import core.main
    from core.general import ReminderService
    from support import mock_notifier

    now = datetime.datetime(2026, 7, 9, 12, 0, 0)          # Thursday
    last_slot = datetime.datetime(2026, 6, 6, 13, 0, 0)    # a Saturday, >4 weeks ago -> due
    with open(os.path.join(str(tmp_path), "general.json"), "w") as f:
        json.dump({"last_reminder": last_slot.strftime(TIMESTAMP_FORMAT)}, f)

    notifier = mock_notifier(has_services=True)
    notifier.notify_reminder.return_value = True

    def seamed_service(config_dir, _notifier):
        # main()'s construction, with the clock and the update check pinned.
        return ReminderService(config_dir, notifier,
                               now_fn=lambda: now, update_check_fn=lambda: False)

    routes = {"/ok/1": [(200, {"price": 150.0, "currency": "€"})]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            _write_config(tmp_path, [
                {"name": "Steady", "url": f"http://{netloc}/ok/1", "target_price": 100.0},
            ])
            with mock.patch.object(sys, "argv", ["main", "--quiet", "--fakestore"]), \
                 mock.patch("core.main.CONFIG_DIR", str(tmp_path)), \
                 mock.patch("core.main.ReminderService", seamed_service), \
                 mock.patch("core.main.Notifier", lambda urls: notifier), \
                 mock.patch("core.ui.config_check.check_env_file"), \
                 mock.patch.dict(os.environ, {"NOTIFICATION_URLS": ""}), \
                 mock.patch.object(ScrapingOrchestrator, "_sleep_with_jitter"), \
                 mock.patch.object(orchestrator_module.signal, "signal"):
                with pytest.raises(SystemExit) as caught:
                    core.main.main()

    assert caught.value.code == EXIT_CODE_SUCCESS
    notifier.notify_reminder.assert_called_once()
    # The persisted slot advanced onto the current grid (Saturday 13:00 <= now),
    # never a moment in the future.
    with open(os.path.join(str(tmp_path), "general.json")) as f:
        recorded = datetime.datetime.strptime(
            json.load(f)["last_reminder"], TIMESTAMP_FORMAT)
    assert last_slot < recorded <= now


def test_main_crash_notifies_and_exits_1(tmp_path):
    """A post-preflight crash takes the modeled path: traceback saved,
    notify_crash sent, exit 1 — not an unhandled traceback."""
    import core.main
    import core.logger
    from support import mock_notifier

    notifier = mock_notifier(has_services=True)
    with registry_sandbox(_fakestore_plugin("127.0.0.1:9")):
        _write_config(tmp_path, [
            {"name": "Widget", "url": "http://127.0.0.1:9/p/1", "target_price": 50.0},
        ])
        with mock.patch.object(sys, "argv", ["main", "--quiet", "--fakestore"]), \
             mock.patch("core.main.CONFIG_DIR", str(tmp_path)), \
             mock.patch("core.main.Notifier", lambda urls: notifier), \
             mock.patch("core.ui.config_check.check_env_file"), \
             mock.patch.dict(os.environ, {"NOTIFICATION_URLS": ""}), \
             mock.patch.object(ScrapingOrchestrator, "run",
                               side_effect=RuntimeError("boom")):
            with pytest.raises(SystemExit) as caught:
                core.main.main()

    assert caught.value.code == EXIT_CODE_ERROR
    notifier.notify_crash.assert_called_once()
    # The traceback landed in the (redirected) root error log.
    with open(os.path.join(core.logger.LOGS_DIR, "errors.txt")) as f:
        assert "RuntimeError: boom" in f.read()


def test_bad_env_in_service_mode_exits_16_before_anything_runs(tmp_path):
    """Service-mode preflight with a really-missing .env: the run exits 16 and
    neither the reminder nor the orchestrator ever starts."""
    import core.main
    import core.utils

    with registry_sandbox(_fakestore_plugin("127.0.0.1:9")):
        _write_config(tmp_path, [
            {"name": "Widget", "url": "http://127.0.0.1:9/p/1", "target_price": 50.0},
        ])
        env_dir = tmp_path / "empty-base"
        env_dir.mkdir()
        with mock.patch.object(sys, "argv", ["main", "--quiet", "--fakestore"]), \
             mock.patch("core.main.CONFIG_DIR", str(tmp_path)), \
             mock.patch.object(core.utils, "BASE_DIR", str(env_dir)), \
             mock.patch("core.main.ReminderService", autospec=True) as reminder_cls, \
             mock.patch("core.main.ScrapingOrchestrator", autospec=True) as orch_cls:
            with pytest.raises(SystemExit) as caught:
                core.main.main()

    assert caught.value.code == EXIT_CODE_ENV_ERROR
    reminder_cls.assert_not_called()
    orch_cls.assert_not_called()
