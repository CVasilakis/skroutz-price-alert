"""Unit tests for pure Configuration Check panel construction."""

import pytest

from core import messages
from core.general.configuration import GeneralConfigLoad
from core.infrastructure.updates import SoftwareVersionStatus
from core.notifications.configuration import NotificationConfig
from core.settings import ResolvedSettings
from core.tui.config_check import build_config_panel, config_view


def _healthy_general() -> GeneralConfigLoad:
    return GeneralConfigLoad(
        notifications=NotificationConfig(valid_urls=("json://localhost",)),
        settings=ResolvedSettings(()),
    )


def test_build_config_panel_uses_collected_update_and_general_config_results():
    panel = build_config_panel(
        _healthy_general(),
        SoftwareVersionStatus(current_version="1.7.0", update_available=None),
        None,
    )

    assert panel.icons == ["🟡", "✅"]
    assert panel.notes == ("Check your internet connection and retry shortly.",)


@pytest.mark.parametrize(
    ("lingering", "expected_icons", "expected_notes"),
    [
        # Undeterminable: no row at all, so nothing about lingering reaches the panel.
        (None, ["✅", "✅"], ()),
        (True, ["✅", "✅", "✅"], ()),
        (
            False,
            ["✅", "🟡", "✅"],
            (
                "Scheduled runs may not happen while you are logged out; enable with "
                "`loginctl enable-linger $USER`.",
            ),
        ),
    ],
)
def test_lingering_row_reports_only_a_determinable_answer(
    lingering, expected_icons, expected_notes
):
    panel = build_config_panel(
        _healthy_general(),
        SoftwareVersionStatus(current_version="1.7.0", update_available=False),
        lingering,
    )

    assert panel.icons == expected_icons
    assert panel.notes == expected_notes


def test_disabled_lingering_only_warns():
    """Lingering is advisory: it tints the panel, it never makes it red."""
    panel = build_config_panel(
        _healthy_general(),
        SoftwareVersionStatus(current_version="1.7.0", update_available=False),
        False,
    )

    assert panel.get_panel_color() == "yellow"


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
