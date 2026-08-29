"""Builders for the Configuration Check panel and the shared 'Config' row.

Two related surfaces. The panel carries the checks that are global to the install
— notification endpoints, general settings, permissions, software version. The
``Config`` row carries one target's own configuration health and is reused atop
each Service Status panel and each Scraping panel, so a user reads the same row in
the same shape wherever a target appears.

Presentation only: it receives already-collected inputs and performs no
configuration, filesystem, network, or systemd access.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape

from core import messages
from core.general import GeneralConfigLoad
from core.infrastructure.updates import SoftwareVersionStatus
from core.presentation import SettingView, resolved_setting_views
from core.settings import SettingStatus
from core.tui.panel import StatusPanelBuilder


@dataclass(frozen=True)
class ConfigView:
    """Presentation summary of a target's target-configuration health (the 'Config' row).

    The single rendering-agnostic model behind the 'Config' row shown atop each Service
    Status panel and Scraping panel (a run). Built by :func:`config_view`
    so the icon/value/footnote decision lives in one place, and consumed by
    :func:`add_config_row` (StatusPanelBuilder panels) and the interactive reporter.

    Attributes:
        icon (str): The status icon (``✅`` / ``🟡`` / ``❗``).
        value (str): The row value as Rich markup, without any footnote reference.
        footnote (str | None): The explanatory note, or ``None`` when healthy.
        has_warning (bool): True for a faulty or failed load (drives the silent-log level).
    """

    icon: str
    value: str
    footnote: str | None = None
    has_warning: bool = False


def config_view(
    count: int,
    faulty_indices: Sequence[int] = (),
    error: str | None = None,
    source_path: str | None = None,
    diagnostic_saved: bool | None = None,
) -> ConfigView:
    """Builds the :class:`ConfigView` for a target from its load outcome.

    Args:
        count (int): The number of loaded items (ignored when ``error`` is set).
        faulty_indices (Sequence[int]): 1-based indices of items failing validation.
        error (str | None): The storage failure message, if the load failed.
        source_path (str | None): Explicit project-relative config path for remediation.
        diagnostic_saved (bool | None): Whether available technical details were logged.

    Returns:
        ConfigView: The icon/value/footnote for the 'Config' row.
    """
    if error is not None:
        if diagnostic_saved is False:
            error = f"{error} {messages.DIAGNOSTIC_WRITE_FAILED}"
        return ConfigView("❗", "[red]Failed[/red]", error, has_warning=True)
    if faulty_indices:
        note = messages.misconfigured_items(source_path)
        if diagnostic_saved is False:
            note = f"{note} {messages.DIAGNOSTIC_WRITE_FAILED}"
        value = f"{count} loaded, [yellow]{len(faulty_indices)} misconfigured[/yellow]"
        return ConfigView("🟡", value, note, has_warning=True)
    return ConfigView("✅", f"{count} loaded", None, has_warning=False)


def add_config_row(panel: StatusPanelBuilder, view: ConfigView) -> None:
    """Renders a :class:`ConfigView` as the 'Config' row on a StatusPanelBuilder panel.

    Used atop the status Service Status panel; the interactive Scraping panel
    renders the same view through the reporter's own footnote mechanism.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (ConfigView): The resolved target-configuration health.
    """
    ref = panel.add_note_ref(view.footnote) if view.footnote else ""
    panel.add_row(view.icon, "Tracked Items", f"{view.value}{ref}")


def add_setting_row(panel: StatusPanelBuilder, view: SettingView) -> None:
    """Renders one resolved setting as a row in the panel's settings section.

    A valid, explicitly-set value shows as ``✅``. An unset value (or a missing config)
    shows its active default as ``✅`` with a dim ``(default)`` marker. An invalid value
    shows the default it fell back to as ``🟡`` plus a footnote naming the problem.

    Shared by the per-scraper Service Status panels (status) and the general
    settings rows of the Configuration Check panel, so every settings row renders
    identically.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (SettingView): The resolved setting (label, display value, status, footnote).
    """
    note_ref = (
        panel.add_note_ref(view.footnote) if view.has_warning and view.footnote is not None else ""
    )
    value = escape(view.display_value)
    if view.has_warning:
        value = f"{value}{note_ref}"
    elif view.is_default:
        value = f"{value} [dim](default)[/dim]"
    icon = "🟡" if view.status in (SettingStatus.INVALID, SettingStatus.MISSING) else "✅"
    panel.add_row(icon, escape(view.label), value)


def _append_version_row(panel: StatusPanelBuilder, status: SoftwareVersionStatus) -> None:
    """Append the already-collected software update result."""
    current = escape(status.current_version or "Unknown")
    if status.non_release_branch is not None:
        branch = escape(status.non_release_branch)
        ref = panel.add_note_ref("You are currently not on the `main` branch.")
        panel.add_row("🟡", "Software Version", f"{current} ({branch} branch){ref}")
    elif status.update_available is None:
        ref = panel.add_note_ref("Check your internet connection and retry shortly.")
        value = f"{current} (Could not check for updates){ref}"
        panel.add_row("🟡", "Software Version", value)
    elif status.update_available:
        ref = panel.add_note_ref("Run `./scrooge-alert update` to install the latest version.")
        update = (
            f"{escape(status.available_version)} available"
            if status.available_version is not None
            else "Minor fixes available"
        )
        panel.add_row("🟡", "Software Version", f"{current} ({update}){ref}")
    else:
        panel.add_row("✅", "Software Version", f"{current} (Up to date)")


def _append_general_rows(panel: StatusPanelBuilder, general: GeneralConfigLoad) -> None:
    """Append already-resolved project-wide settings from ``config/general.json``.

    Mirrors the per-scraper settings section of the Service Status panels, so the general
    settings get the same invalid-value UX. Malformed blocks and unknown keys fail the
    strict general-config boundary. Iterates whatever ``GENERAL_SETTING_SPECS`` declares.
    """
    if general.settings is None:
        detail = general.settings_error or "General settings are unavailable."
        if general.diagnostic_saved is False:
            detail = f"{detail} {messages.DIAGNOSTIC_WRITE_FAILED}"
        ref = panel.add_note_ref(detail)
        panel.add_row("❗", "General Config", f"[red]Failed{ref}[/red]")
        return
    for view in resolved_setting_views(general.settings):
        add_setting_row(panel, view)


def _append_notifications_row(panel: StatusPanelBuilder, general: GeneralConfigLoad) -> None:
    """Append the redacted notification health and advisory permission footnote."""
    notifications = general.notifications
    permission_ref = (
        panel.add_note_ref(general.permission_warning) if general.permission_warning else ""
    )

    if notifications.valid_urls:
        if notifications.invalid_urls or permission_ref:
            invalid_ref = (
                panel.add_note_ref("Run `./scrooge-alert ping` for more details.")
                if notifications.invalid_urls
                else ""
            )
            invalid_text = (
                f", [yellow]{len(notifications.invalid_urls)} invalid{invalid_ref}[/yellow]"
                if notifications.invalid_urls
                else ""
            )
            panel.add_row(
                "🟡",
                "Notifications",
                f"{len(notifications.valid_urls)} valid URL(s){invalid_text}{permission_ref}",
            )
        else:
            panel.add_row("✅", "Notifications", f"{len(notifications.valid_urls)} valid URL(s)")
    elif notifications.invalid_urls:
        detail_ref = panel.add_note_ref("Run `./scrooge-alert ping` for more details.")
        panel.add_row(
            "❗",
            "Notifications",
            f"[red]0 valid URL(s), {len(notifications.invalid_urls)} invalid"
            f"{detail_ref}{permission_ref}[/red]",
        )
    else:
        detail = notifications.error or "No notification URLs found in `config/general.json`."
        if general.diagnostic_saved is False:
            detail = f"{detail} {messages.DIAGNOSTIC_WRITE_FAILED}"
        ref = panel.add_note_ref(detail)
        panel.add_row("❗", "Notifications", f"[red]Not configured{ref}{permission_ref}[/red]")


def build_config_panel(
    general: GeneralConfigLoad, version_status: SoftwareVersionStatus
) -> StatusPanelBuilder:
    """Build the global configuration panel from already-collected inputs."""
    panel = StatusPanelBuilder("Configuration Check")
    _append_version_row(panel, version_status)
    _append_notifications_row(panel, general)
    _append_general_rows(panel, general)
    return panel


def render_config_panel(
    console: Console, general: GeneralConfigLoad, version_status: SoftwareVersionStatus
) -> None:
    """Builds and renders the shared 'Configuration Check' panel (global checks only).

    Renders already-collected update and project-wide configuration results.
    Per-scraper target-configuration health is intentionally not shown here — it is surfaced
    as a 'Config' row atop each Service Status panel (status) and Scraping panel
    (a run). This is the single presentation path shared by the interactive scraper run
    (run.py) and the health check (status.py); it performs no config-file I/O itself.

    Args:
        console (Console): The Rich console to render to.
    """
    build_config_panel(general, version_status).render(console)


__all__ = [
    "ConfigView",
    "add_config_row",
    "add_setting_row",
    "build_config_panel",
    "config_view",
    "render_config_panel",
]
