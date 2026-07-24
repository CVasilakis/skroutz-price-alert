"""Entry-point wiring tests for ``core.ping``."""

from pathlib import Path
from unittest import mock

import core.infrastructure.logging
import core.ping
from core.general.configuration import GeneralConfigLoad
from core.notifications.configuration import NotificationConfig
from core.settings import ResolvedSettings


def test_main_validates_only_nonblank_urls_and_renders_results():
    console = mock.MagicMock()
    status_context = console.status.return_value
    status_context.__enter__.return_value = None
    status_context.__exit__.return_value = None

    with (
        mock.patch("core.ping.install_interrupt_handler"),
        mock.patch("core.ping.setup_global_logging"),
        mock.patch(
            "core.ping.load_general_config",
            return_value=GeneralConfigLoad(
                NotificationConfig(
                    configured_urls=("json://first", "broken", "", "json://second"),
                    valid_urls=("json://first", "json://second"),
                    invalid_urls=("broken", ""),
                ),
                ResolvedSettings(()),
            ),
        ),
        mock.patch("core.ping.AppriseNotifier") as notifier_type,
        mock.patch("core.ping.Console", return_value=console),
        mock.patch("core.ping.signal.signal"),
        mock.patch("core.ping.build_ping_panel") as build_panel,
    ):
        notifier_type.return_value.notify_test.return_value = [
            ("first", True),
            ("second", False),
        ]
        panel = mock.MagicMock()
        build_panel.return_value = (panel, "yellow")
        core.ping.main()

    notifier_type.assert_called_once_with(["json://first", "json://second"])
    build_panel.assert_called_once_with(
        [
            ("json://first", True),
            ("broken", False),
            ("", False),
            ("json://second", True),
        ],
        [("first", True), ("second", False)],
        "",
    )
    panel.render.assert_called_once_with(console, panel_color="yellow")


def test_main_reports_config_error_without_constructing_notifier():
    console = mock.MagicMock()
    with (
        mock.patch("core.ping.install_interrupt_handler"),
        mock.patch("core.ping.setup_global_logging"),
        mock.patch(
            "core.ping.load_general_config",
            return_value=GeneralConfigLoad(
                NotificationConfig(error="General config is unreadable"),
                None,
                settings_error="General config is unreadable",
                diagnostic=(
                    "Path: /absolute/config/general.json\nException: PermissionError\nErrno: 13"
                ),
            ),
        ),
        mock.patch("core.ping.AppriseNotifier") as notifier_type,
        mock.patch("core.ping.Console", return_value=console),
        mock.patch("core.ping.signal.signal"),
        mock.patch("core.ping.build_ping_panel") as build_panel,
    ):
        panel = mock.MagicMock()
        build_panel.return_value = (panel, "red")
        core.ping.main()

    notifier_type.assert_not_called()
    build_panel.assert_called_once_with([], [], "General config is unreadable")
    content = (Path(core.infrastructure.logging.LOGS_DIR) / "errors.txt").read_text()
    assert "Path: /absolute/config/general.json" in content
    assert "Errno: 13" in content
