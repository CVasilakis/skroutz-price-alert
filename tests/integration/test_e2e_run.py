"""Real Python entrypoint -> config -> client -> state/logging integration."""

import json
import logging
import sys
from pathlib import Path
from unittest import mock

import pytest
from support import catalog_sandbox, fake_plugin, mock_notifier

import core.run
from core.exit_status import ExitStatus
from core.scrapers.framework.catalog import PluginCatalog
from integration.fake_store import URL, FakeStoreClient, fake_store_server


def _write_general(config_dir: Path, *, usable_notifications: bool = True) -> None:
    urls = ["json://localhost"] if usable_notifications else []
    (config_dir / "general.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "notifications": {"urls": urls},
                "settings": {"reminder": "off"},
            }
        ),
        encoding="utf-8",
    )


def _write_target(config_dir: Path, target: str, url: str) -> Path:
    path = config_dir / f"{target}.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin_schema_version": 1,
                "settings": {},
                "items": [
                    {
                        "id": "widget",
                        "name": "Widget",
                        "url": url,
                        "target_price": 100,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _restore_logging_state():
    root = logging.root
    original_handlers = root.handlers[:]
    original_level = root.level
    yield
    for handler in root.handlers[:]:
        if handler not in original_handlers:
            handler.close()
        root.removeHandler(handler)
    for handler in original_handlers:
        root.addHandler(handler)
    root.setLevel(original_level)
    for name, logger in logging.Logger.manager.loggerDict.items():
        if not name.startswith("scraper.") or not isinstance(logger, logging.Logger):
            continue
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


def _invoke_run(
    monkeypatch,
    catalog: PluginCatalog,
    config_dir: Path,
    state_dir: Path,
    notifier,
    *args: str,
) -> int:
    monkeypatch.setattr(sys, "argv", ["run", *args])
    monkeypatch.setattr(core.run, "CONFIG_DIR", str(config_dir))
    monkeypatch.setattr(core.run, "STATE_DIR", str(state_dir))
    monkeypatch.setattr(core.run.PluginCatalog, "discover", mock.Mock(return_value=catalog))
    monkeypatch.setattr(core.run, "AppriseNotifier", mock.Mock(return_value=notifier))
    monkeypatch.setattr("core.application.pacing.Pacer.sleep", mock.Mock())
    monkeypatch.setattr("core.application.orchestrator.signal.signal", mock.Mock())
    with pytest.raises(SystemExit) as caught:
        core.run.main()
    return caught.value.code


def test_quiet_selected_target_is_silent_and_writes_state_and_output_log(
    tmp_path, monkeypatch, capfd
):
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
            config_path = _write_target(config_dir, "fakestore", f"http://{netloc}/widget#fragment")
            original = config_path.read_bytes()
            _write_general(config_dir)
            notifier = mock_notifier(True)

            code = _invoke_run(
                monkeypatch,
                catalog,
                config_dir,
                state_dir,
                notifier,
                "--quiet",
                "--fakestore",
            )

    assert code == ExitStatus.SUCCESS
    assert capfd.readouterr() == ("", "")
    assert config_path.read_bytes() == original
    state = json.loads((state_dir / "fakestore.json").read_text())
    assert state["items"]["widget"]["last_price"] == 79.0
    assert state["items"]["widget"]["price_alert_delivered"] is True
    notifier.notify_low_price.assert_called_once()
    output = (tmp_path / "logs" / "fakestore" / "output.log").read_text()
    assert "Tracked Items: 1 loaded" in output
    assert "Widget: 79.0 EUR" in output
    assert " UTC]" in output


def test_quiet_default_run_isolates_bad_config_and_continues_healthy_target(
    tmp_path, monkeypatch, capfd
):
    with fake_store_server({"/healthy": [(200, {"price": 120, "currency": "EUR"})]}) as (
        server,
        netloc,
    ):
        broken = fake_plugin(
            name="brokenstore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        healthy = fake_plugin(
            name="healthystore",
            domains=("127.0.0.1",),
            client_class=FakeStoreClient,
            url_field=URL,
        )
        with catalog_sandbox(broken, healthy) as catalog:
            config_dir, state_dir = tmp_path / "config", tmp_path / "state"
            config_dir.mkdir()
            (config_dir / "brokenstore.json").write_text("{broken", encoding="utf-8")
            _write_target(config_dir, "healthystore", f"http://{netloc}/healthy")
            _write_general(config_dir)

            code = _invoke_run(
                monkeypatch,
                catalog,
                config_dir,
                state_dir,
                mock_notifier(True),
                "--quiet",
            )

    assert code == ExitStatus.TARGET_CONFIG_ERROR
    assert capfd.readouterr() == ("", "")
    assert server.request_count == 1
    assert (state_dir / "healthystore.json").exists()
    assert not (state_dir / "brokenstore.json").exists()
    assert "Tracked Items: Failed" in (tmp_path / "logs" / "brokenstore" / "output.log").read_text()


def test_quiet_notification_preflight_is_silent_and_never_scrapes(tmp_path, monkeypatch, capfd):
    plugin = fake_plugin(
        name="fakestore",
        domains=("127.0.0.1",),
        client_class=FakeStoreClient,
        url_field=URL,
    )
    with catalog_sandbox(plugin) as catalog:
        config_dir, state_dir = tmp_path / "config", tmp_path / "state"
        config_dir.mkdir()
        _write_target(config_dir, "fakestore", "http://127.0.0.1:1/widget")
        _write_general(config_dir, usable_notifications=False)
        notifier = mock_notifier(False)

        code = _invoke_run(
            monkeypatch,
            catalog,
            config_dir,
            state_dir,
            notifier,
            "--quiet",
            "--fakestore",
        )

    assert code == ExitStatus.NOTIFICATION_CONFIG_ERROR
    assert capfd.readouterr() == ("", "")
    assert not state_dir.exists()
    assert (
        "Notification configuration failed"
        in (tmp_path / "logs" / "fakestore" / "output.log").read_text()
    )


def test_quiet_startup_crash_is_silent_and_saved_to_root_log(tmp_path, monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv", ["run", "--quiet"])
    monkeypatch.setattr(
        core.run.PluginCatalog,
        "discover",
        mock.Mock(side_effect=RuntimeError("catalog exploded")),
    )

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == ExitStatus.APPLICATION_ERROR
    assert capfd.readouterr() == ("", "")
    assert "catalog exploded" in (tmp_path / "logs" / "errors.txt").read_text()


def test_quiet_runtime_crash_is_silent_saved_and_notified(tmp_path, monkeypatch, capfd):
    plugin = fake_plugin(
        name="fakestore",
        domains=("127.0.0.1",),
        client_class=FakeStoreClient,
        url_field=URL,
    )
    with catalog_sandbox(plugin) as catalog:
        config_dir, state_dir = tmp_path / "config", tmp_path / "state"
        config_dir.mkdir()
        _write_target(config_dir, "fakestore", "http://127.0.0.1:1/widget")
        _write_general(config_dir)
        notifier = mock_notifier(True)
        monkeypatch.setattr(
            core.run.ScrapingOrchestrator,
            "run",
            mock.Mock(side_effect=RuntimeError("orchestrator exploded")),
        )

        code = _invoke_run(
            monkeypatch,
            catalog,
            config_dir,
            state_dir,
            notifier,
            "--quiet",
            "--fakestore",
        )

    assert code == ExitStatus.APPLICATION_ERROR
    assert capfd.readouterr() == ("", "")
    assert "orchestrator exploded" in (tmp_path / "logs" / "errors.txt").read_text()
    notifier.notify_crash.assert_called_once()


def test_quiet_does_not_hide_explicit_help(monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv", ["run", "--quiet", "--help"])
    monkeypatch.setattr(
        core.run.PluginCatalog,
        "discover",
        mock.Mock(return_value=PluginCatalog(())),
    )

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 0
    captured = capfd.readouterr()
    assert "--quiet" in captured.out
    assert captured.err == ""


def test_quiet_does_not_hide_invalid_argument_usage(monkeypatch, capfd):
    monkeypatch.setattr(sys, "argv", ["run", "--quiet", "--unknown"])
    monkeypatch.setattr(
        core.run.PluginCatalog,
        "discover",
        mock.Mock(return_value=PluginCatalog(())),
    )

    with pytest.raises(SystemExit) as caught:
        core.run.main()

    assert caught.value.code == 2
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --unknown" in captured.err
