"""Unit tests for pure Configuration Check panel construction."""

from core import messages
from core.general.configuration import GeneralConfigLoad
from core.notifications.configuration import NotificationConfig
from core.settings import ResolvedSettings
from core.tui.config_check import build_config_panel, config_view


def test_build_config_panel_uses_collected_update_and_general_config_results():
    general = GeneralConfigLoad(
        notifications=NotificationConfig(valid_urls=("json://localhost",)),
        settings=ResolvedSettings(()),
    )

    panel = build_config_panel(general, update_available=None)

    assert panel.icons == ["🟡", "✅"]
    assert panel.notes == ("Check your internet connection and retry shortly.",)


def test_config_view_discloses_failed_diagnostic_write():
    view = config_view(
        1,
        (2,),
        source_path="config/store.json",
        diagnostic_saved=False,
    )

    assert view.footnote is not None
    assert "Fix items in `config/store.json`" in view.footnote
    assert messages.DIAGNOSTIC_WRITE_FAILED in view.footnote
