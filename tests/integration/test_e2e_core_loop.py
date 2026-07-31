"""Disk config -> HTTP client -> orchestrator -> separate JSON state."""

import json
import logging
from datetime import datetime, timezone
from unittest import mock

from support import catalog_sandbox, fake_plugin, mock_notifier, mock_ui

from core.application.contracts import PriceOutcome
from core.application.orchestrator import ScrapingOrchestrator
from core.application.preflight import load_target_configs
from core.exit_status import ExitStatus
from core.infrastructure.locking import StateLockManager
from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
from core.scrapers.framework.clients import ClientLoader
from integration.fake_store import URL, FakeStoreClient, fake_store_server

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)


def _write_config(config_dir, url=None, *, extra=None, settings=None, items=None):
    if items is None:
        items = [
            {
                "id": "widget",
                "name": "Widget",
                "url": url,
                "target_price": 100.0,
                **(extra or {}),
            }
        ]
    document = {
        "schema_version": 1,
        "plugin_schema_version": 1,
        "settings": settings or {},
        "items": items,
    }
    path = config_dir / "fakestore.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _run(catalog, config_dir, state_dir, notifier, ui):
    loader = ClientLoader()
    loads = load_target_configs([catalog.get("fakestore")], str(config_dir))
    orchestrator = ScrapingOrchestrator(
        loads,
        loader,
        notifier,
        quiet=True,
        reporter=ui,
        now_fn=lambda: NOW,
        state_dir=str(state_dir),
    )
    logger = logging.getLogger("e2e")
    with (
        mock.patch("core.application.pacing.Pacer.sleep"),
        mock.patch("core.application.orchestrator.signal.signal"),
        mock.patch("core.application.orchestrator.get_target_logger", return_value=logger),
    ):
        return orchestrator.run()


def test_real_http_scrape_keeps_config_read_only_and_writes_state(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79, "currency": "EUR"})]}) as (
        _server,
        netloc,
    ):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            config_path = _write_config(config_dir, f"http://{netloc}/widget")
            original_config = config_path.read_bytes()
            notifier, ui = mock_notifier(True), mock_ui()
            code = _run(catalog, config_dir, state_dir, notifier, ui)

    assert code == ExitStatus.SUCCESS
    assert config_path.read_bytes() == original_config
    state = json.loads((state_dir / "fakestore.json").read_text())
    assert state == {
        "schema_version": 1,
        "items": {
            "widget": {
                "last_price": 79.0,
                "last_checked": "2026-07-18T18:30:00Z",
                "price_alert_delivered": True,
            }
        },
    }
    notifier.notify_low_price.assert_called_once()
    assert ui.log_price_result.call_args.args[4] is PriceOutcome.DROP


def test_malformed_row_never_reaches_the_network_or_creates_state(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79})]}) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(config_dir, f"http://{netloc}/widget", extra={"unknown": True})
            notifier, ui = mock_notifier(True), mock_ui()
            assert _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.SUCCESS

    assert server.request_count == 0
    assert not (state_dir / "fakestore.json").exists()
    notifier.notify_low_price.assert_not_called()


def test_successful_alert_history_suppresses_the_next_run(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79, "currency": "EUR"})]}) as (
        server,
        netloc,
    ):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(
                config_dir,
                f"http://{netloc}/widget",
                settings={"suppress_repeated_price_alerts": True},
            )
            notifier, ui = mock_notifier(True), mock_ui()

            assert _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.SUCCESS
            assert _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.SUCCESS

    assert server.request_count == 2
    notifier.notify_low_price.assert_called_once()
    state = json.loads((state_dir / "fakestore.json").read_text())
    assert state["items"]["widget"]["price_alert_delivered"] is True


def test_transient_server_error_retries_then_persists_success(tmp_path):
    with fake_store_server(
        {"/widget": [(500, None), (200, {"price": 120, "currency": "EUR"})]}
    ) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(config_dir, f"http://{netloc}/widget")
            notifier, ui = mock_notifier(True), mock_ui()
            code = _run(catalog, config_dir, state_dir, notifier, ui)

    assert code == ExitStatus.SUCCESS
    assert server.request_count == 2
    assert (
        json.loads((state_dir / "fakestore.json").read_text())["items"]["widget"]["last_price"]
        == 120.0
    )
    assert ui.log_attempt.call_count == 1


def test_rate_limit_exhaustion_aborts_remaining_items(tmp_path):
    with fake_store_server(
        {
            "/limited": [(429, None)],
            "/untouched": [(200, {"price": 50, "currency": "EUR"})],
        }
    ) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(
                config_dir,
                items=[
                    {
                        "id": "limited",
                        "name": "Limited",
                        "url": f"http://{netloc}/limited",
                        "target_price": 100,
                    },
                    {
                        "id": "untouched",
                        "name": "Untouched",
                        "url": f"http://{netloc}/untouched",
                        "target_price": 100,
                    },
                ],
            )
            code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.RATE_LIMIT_ERROR
    assert server.request_count == 3
    assert not (state_dir / "fakestore.json").exists()
    assert (tmp_path / "logs" / "fakestore" / "errors.txt").exists()


def test_exhausted_parser_fault_writes_diagnostic_and_no_state(tmp_path):
    with fake_store_server({"/widget": [(200, b"{not-json")]}) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(config_dir, f"http://{netloc}/widget")
            code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.SCRAPE_ERROR
    assert server.request_count == 3
    assert not (state_dir / "fakestore.json").exists()
    assert "JSONDecodeError" in (tmp_path / "logs" / "fakestore" / "errors.txt").read_text()


def test_malformed_existing_state_is_preserved_without_network_access(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 50})]}) as (server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            state_dir.mkdir()
            _write_config(config_dir, f"http://{netloc}/widget")
            state_path = state_dir / "fakestore.json"
            malformed = b'{"schema_version": 1, "items": []}'
            state_path.write_bytes(malformed)
            code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.STORAGE_ERROR
    assert server.request_count == 0
    assert state_path.read_bytes() == malformed


def test_failed_price_alert_is_retried_until_successful_history_is_persisted(tmp_path):
    with fake_store_server({"/widget": [(200, {"price": 79, "currency": "EUR"})]}) as (
        _server,
        netloc,
    ):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(
                config_dir,
                f"http://{netloc}/widget",
                settings={"suppress_repeated_price_alerts": True},
            )
            notifier, ui = mock_notifier(True, delivery_ok=False), mock_ui()
            assert (
                _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.NOTIFICATION_ERROR
            )
            first = json.loads((state_dir / "fakestore.json").read_text())
            assert "price_alert_delivered" not in first["items"]["widget"]

            notifier.notify_low_price.return_value = True
            assert _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.SUCCESS

    assert notifier.notify_low_price.call_count == 2
    final = json.loads((state_dir / "fakestore.json").read_text())
    assert final["items"]["widget"]["price_alert_delivered"] is True


def test_listing_history_retries_only_failed_offer_urls(tmp_path):
    payload = {
        "currency": "EUR",
        "offers": [
            {"title": "A", "price": 70, "url": "https://offers.example/a"},
            {"title": "B", "price": 80, "url": "https://offers.example/b"},
            {"title": "Too expensive", "price": 120, "url": "https://offers.example/c"},
        ],
    }
    with fake_store_server({"/widget": [(200, payload)]}) as (_server, netloc):
        plugin = fake_plugin(
            name="fakestore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(plugin) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            _write_config(
                config_dir,
                f"http://{netloc}/widget",
                settings={"suppress_repeated_price_alerts": True},
            )
            notifier, ui = mock_notifier(True), mock_ui()
            notifier.notify_low_price.side_effect = [True, False]
            assert (
                _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.NOTIFICATION_ERROR
            )
            first = json.loads((state_dir / "fakestore.json").read_text())
            assert first["items"]["widget"]["notified_offer_urls"] == ["https://offers.example/a"]

            notifier.notify_low_price.side_effect = None
            notifier.notify_low_price.return_value = True
            assert _run(catalog, config_dir, state_dir, notifier, ui) == ExitStatus.SUCCESS

    assert notifier.notify_low_price.call_count == 3
    final = json.loads((state_dir / "fakestore.json").read_text())
    assert final["items"]["widget"]["notified_offer_urls"] == [
        "https://offers.example/a",
        "https://offers.example/b",
    ]


class _MissingDependencyClient(ScraperClient):
    def __init__(self, _settings):
        raise ImportError("No module named 'private_transport'", name="private_transport")

    def scrape(self, _item: TrackedItem) -> PriceResult:
        raise AssertionError("scrape must not be reached")


def test_missing_private_dependency_has_runtime_exit_without_state_mutation(tmp_path):
    plugin = fake_plugin(
        name="fakestore",
        domains=("127.0.0.1",),
        client_class=_MissingDependencyClient,
        url_field=URL,
    )
    with catalog_sandbox(plugin) as catalog:
        config_dir, state_dir = tmp_path / "config", tmp_path / "state"
        config_dir.mkdir()
        _write_config(config_dir, "http://127.0.0.1:1/widget")
        code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.PLUGIN_DEPENDENCY_ERROR
    assert not (state_dir / "fakestore.json").exists()


def test_real_lock_contention_skips_before_client_or_state_access(tmp_path):
    plugin = fake_plugin(
        name="fakestore",
        domains=("127.0.0.1",),
        client_class=FakeStoreClient,
        url_field=URL,
    )
    with catalog_sandbox(plugin) as catalog:
        config_dir, state_dir = tmp_path / "config", tmp_path / "state"
        config_dir.mkdir()
        _write_config(config_dir, "http://127.0.0.1:1/widget")
        with StateLockManager(state_dir).acquire("fakestore"):
            code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.ALREADY_RUNNING
    assert (state_dir / "locks" / "fakestore.lock").exists()
    assert not (state_dir / "fakestore.json").exists()


def test_unsafe_lock_storage_returns_storage_status_without_following_symlink(tmp_path):
    plugin = fake_plugin(
        name="fakestore",
        domains=("127.0.0.1",),
        client_class=FakeStoreClient,
        url_field=URL,
    )
    with catalog_sandbox(plugin) as catalog:
        config_dir, state_dir = tmp_path / "config", tmp_path / "state"
        config_dir.mkdir()
        state_dir.mkdir()
        _write_config(config_dir, "http://127.0.0.1:1/widget")
        outside = tmp_path / "outside"
        outside.mkdir()
        (state_dir / "locks").symlink_to(outside, target_is_directory=True)

        code = _run(catalog, config_dir, state_dir, mock_notifier(True), mock_ui())

    assert code == ExitStatus.STORAGE_ERROR
    assert not (outside / "fakestore.lock").exists()
    assert not (state_dir / "fakestore.json").exists()
