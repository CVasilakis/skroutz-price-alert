"""End-to-end tests of the core execution loop against a real local HTTP server.

These drive the full chain — config file on disk -> preflight ``load_targets`` ->
real ``ScrapingOrchestrator`` -> real registry -> a urllib scraper client hitting
``http.server`` -> real JSON storage write-back — with only two surgical patches
(the pacing sleep and the signal-handler install). The fake store lives in
``tests/fake_store.py`` and is registered through the shared ``registry_sandbox``,
so no real plugin (or its dependencies) is involved; the whole suite runs on the
stdlib and finishes in about a second.

The UI strategy is the autospec'd ``ExecutionStrategy`` double so each case can
assert the exact rendered payload (``core.messages``); one final test goes through
``core.main.main()`` itself with the real ``SilentExecutionStrategy`` to prove the
argparse -> preflight -> reminder -> orchestrator wiring end to end.
"""

import json
import os
import sys
from unittest import mock

import pytest

from core import messages
from core import orchestrator as orchestrator_module
from core.constants import (
    MAX_RETRIES,
    EXIT_CODE_SUCCESS, EXIT_CODE_RATE_LIMIT_ERROR, EXIT_CODE_PRODUCTS_ERROR,
)
from core.locks import acquire_lock
from core.orchestrator import ScrapingOrchestrator
from core.scrapers.registry import ScraperRegistry
from core.ui.config_check import load_targets
from core.ui.tui import PriceOutcome

from fake_store import FakeStoreClient, FakeStoreDataManager, fake_store_server
from support import fake_plugin, mock_notifier, mock_ui, registry_sandbox


def _fakestore_plugin(netloc):
    """The fake store bound to the live server's netloc (domain match includes the port)."""
    return fake_plugin(name="fakestore", domains=(netloc,), config="fakestore.json",
                       client_class=FakeStoreClient, storage_class=FakeStoreDataManager)


def _write_config(cfg_dir, products):
    path = os.path.join(str(cfg_dir), "fakestore.json")
    with open(path, "w") as f:
        json.dump({"products": products}, f)
    return path


def _run_orchestrator(cfg_dir, notifier, ui):
    """Runs the real orchestrator over the fakestore target with real components.

    Only the pacing sleep and the signal-handler install are patched; everything
    else (registry, settings resolution, storage, client, locks) is production code.
    """
    registry = ScraperRegistry(str(cfg_dir))
    loads_by_target = {tl.target: tl for tl in load_targets(registry, ["fakestore"])}
    orch = ScrapingOrchestrator(
        targets_to_run=["fakestore"], registry=registry, notifier=notifier,
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


def test_product_gone_is_a_warning_not_a_failure(tmp_path):
    routes = {"/gone/1": [(404, None)]}
    with fake_store_server(routes) as netloc:
        with registry_sandbox(_fakestore_plugin(netloc)):
            _write_config(tmp_path, [
                {"name": "Removed", "url": f"http://{netloc}/gone/1", "target_price": 50.0},
            ])
            notifier, ui = mock_notifier(has_services=True), mock_ui()

            exit_code = _run_orchestrator(tmp_path, notifier, ui)

    assert exit_code == EXIT_CODE_SUCCESS
    ui.log_warning.assert_called_once_with(
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
    notifier.notify_errors.assert_called_once()


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
