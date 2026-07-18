"""Disk config -> HTTP client -> orchestrator -> separate JSON state."""

import json
import logging
from datetime import datetime, timezone
from unittest import mock

from core.constants import EXIT_CODE_SUCCESS
from core.orchestrator import ScrapingOrchestrator
from core.preflight import load_targets
from core.scrapers.registry import ScraperRegistry
from core.ui.tui import PriceOutcome
from integration.fake_store_v1 import FakeStoreClient, fake_store_server
from support import fake_plugin, mock_notifier, mock_ui, registry_sandbox


NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)


def _write_config(config_dir, url, *, extra=None):
    document = {
        "settings": {},
        "items": [{
            "id": "widget", "name": "Widget", "url": url,
            "target_price": 100.0, **(extra or {}),
        }],
    }
    path = config_dir / "fakestore.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(config_dir, state_dir, notifier, ui):
    registry = ScraperRegistry(str(config_dir), str(state_dir))
    loads = load_targets(registry, ["fakestore"])
    orchestrator = ScrapingOrchestrator(
        loads, registry, notifier, quiet=True, ui_strategy=ui, now_fn=lambda: NOW,
    )
    logger = logging.getLogger("e2e-v1")
    with mock.patch.object(orchestrator, "_sleep_with_jitter"), \
         mock.patch("core.orchestrator.signal.signal"), \
         mock.patch("core.orchestrator.get_target_logger", return_value=logger):
        return orchestrator.run()


def test_real_http_scrape_keeps_config_read_only_and_writes_state(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79, "currency": "EUR"})]}) as (_server, netloc):
        plugin = fake_plugin(
            name="fakestore", domains=("127.0.0.1",), client_class=FakeStoreClient,
        )
        with registry_sandbox(plugin):
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            config_path = _write_config(config_dir, f"http://{netloc}/widget")
            original_config = config_path.read_bytes()
            notifier, ui = mock_notifier(True), mock_ui()
            code = _run(config_dir, state_dir, notifier, ui)

    assert code == EXIT_CODE_SUCCESS
    assert config_path.read_bytes() == original_config
    state = json.loads((state_dir / "fakestore.json").read_text())
    assert state == {
        "schema_version": 1,
        "items": {"widget": {
            "last_price": 79.0, "last_checked": "2026-07-18T18:30:00Z",
        }},
    }
    notifier.notify_low_price.assert_called_once()
    assert ui.log_price_result.call_args.args[4] is PriceOutcome.DROP


def test_malformed_row_never_reaches_the_network_or_creates_state(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79})]}) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore", domains=("127.0.0.1",), client_class=FakeStoreClient,
        )
        with registry_sandbox(plugin):
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(config_dir, f"http://{netloc}/widget", extra={"unknown": True})
            notifier, ui = mock_notifier(True), mock_ui()
            assert _run(config_dir, state_dir, notifier, ui) == EXIT_CODE_SUCCESS

    assert server.request_count == 0
    assert not (state_dir / "fakestore.json").exists()
    notifier.notify_low_price.assert_not_called()
