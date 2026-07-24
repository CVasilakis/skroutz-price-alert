from collections.abc import Sequence
from dataclasses import dataclass

from rich.console import Console
from rich.markup import escape

from core.general.configuration import GeneralConfigLoad
from core.tui.panel import StatusPanelBuilder


@dataclass(frozen=True)
class ConfigView:
    """Presentation summary of a target's products-config health (the 'Config' row).

    The single rendering-agnostic model behind the 'Config' row shown atop each Service
    Status panel (``--status``) and Scraping panel (a run). Built by :func:`config_view`
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
) -> ConfigView:
    """Builds the :class:`ConfigView` for a target from its load outcome.

    Args:
        count (int): The number of loaded items (ignored when ``error`` is set).
        faulty_indices (Sequence[int]): 1-based indices of items failing validation.
        error (str | None): The storage failure message, if the load failed.
        source_path (str | None): Explicit project-relative config path for remediation.

    Returns:
        ConfigView: The icon/value/footnote for the 'Config' row.
    """
    if error is not None:
        return ConfigView("❗", "[red]Failed[/red]", error, has_warning=True)
    if faulty_indices:
        note = (
            f"Fix items in `{source_path}`; details are logged."
            if source_path
            else "Fix misconfigured items; details are logged."
        )
        value = f"{count} loaded, [yellow]{len(faulty_indices)} misconfigured[/yellow]"
        return ConfigView("🟡", value, note, has_warning=True)
    return ConfigView("✅", f"{count} loaded", None, has_warning=False)


def add_config_row(panel: StatusPanelBuilder, view: ConfigView) -> None:
    """Renders a :class:`ConfigView` as the 'Config' row on a StatusPanelBuilder panel.

    Used atop the ``--status`` Service Status panel; the interactive Scraping panel
    renders the same view through the reporter's own footnote mechanism.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (ConfigView): The resolved products-config health.
    """
    ref = panel.add_note_ref(view.footnote) if view.footnote else ""
    panel.add_row(view.icon, "Monitored Items", f"{view.value}{ref}")


def add_setting_row(panel: StatusPanelBuilder, view) -> None:
    """Renders one resolved setting as a row in the panel's settings section.

    A valid, explicitly-set value shows as ``✅``. An unset value (or a missing config)
    shows its active default as ``✅`` with a dim ``(default)`` marker. An invalid value
    shows the default it fell back to as ``🟡`` plus a footnote naming the problem.

    Shared by the per-scraper Service Status panels (``--status``) and the general
    settings rows of the Configuration Check panel, so every settings row renders
    identically.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (SettingView): The resolved setting (label, display value, status, footnote).
    """
    note_ref = panel.add_note_ref(view.footnote) if view.has_warning else ""
    value = view.render_value(
        note_ref,
        default_marker=" [dim](default)[/dim]",
        value_text=escape(view.display_value),
    )
    panel.add_row(view.icon, escape(view.label), value)


def _append_version_row(panel: StatusPanelBuilder, update_available: bool | None) -> None:
    """Append the already-collected software update result."""
    if update_available is None:
        ref = panel.add_note_ref("Check your internet connection and retry shortly.")
        panel.add_row("🟡", "Software Version", f"Could not check for updates{ref}")
    elif update_available:
        ref = panel.add_note_ref("Run `./update.sh` to install the latest version.")
        panel.add_row("🟡", "Software Version", f"Update available!{ref}")
    else:
        panel.add_row("✅", "Software Version", "Up to date")


def _append_general_rows(panel: StatusPanelBuilder, general: GeneralConfigLoad) -> None:
    """Append already-resolved project-wide settings from ``config/general.json``.

    Mirrors the per-scraper settings section of the Service Status panels, so the general
    settings get the same invalid-value UX. Malformed blocks and unknown keys fail the
    strict general-config boundary. Iterates whatever ``GENERAL_SETTING_SPECS`` declares.
    """
    if general.settings is None:
        ref = panel.add_note_ref(general.settings_error or "General settings are unavailable.")
        panel.add_row("❗", "General Config", f"[red]Failed{ref}[/red]")
        return
    for view in general.settings.views():
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
                panel.add_note_ref("Run `./scripts/run.sh --ping` for more details.")
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
        detail_ref = panel.add_note_ref("Run `./scripts/run.sh --ping` for more details.")
        panel.add_row(
            "❗",
            "Notifications",
            f"[red]0 valid URL(s), {len(notifications.invalid_urls)} invalid"
            f"{detail_ref}{permission_ref}[/red]",
        )
    else:
        detail = notifications.error or "No notification URLs found in `config/general.json`."
        ref = panel.add_note_ref(detail)
        panel.add_row("❗", "Notifications", f"[red]Not configured{ref}{permission_ref}[/red]")


def build_config_panel(
    general: GeneralConfigLoad, update_available: bool | None
) -> StatusPanelBuilder:
    """Build the global configuration panel from already-collected inputs."""
    panel = StatusPanelBuilder("Configuration Check")
    _append_version_row(panel, update_available)
    _append_notifications_row(panel, general)
    _append_general_rows(panel, general)
    return panel


def render_config_panel(
    console: Console, general: GeneralConfigLoad, update_available: bool | None
) -> None:
    """Builds and renders the shared 'Configuration Check' panel (global checks only).

    Renders already-collected update and project-wide configuration results.
    Per-scraper products-config health is intentionally not shown here — it is surfaced
    as a 'Config' row atop each Service Status panel (``--status``) and Scraping panel
    (a run). This is the single presentation path shared by the interactive scraper run
    (main.py) and the health check (status.py); it performs no config-file I/O itself.

    Args:
        console (Console): The Rich console to render to.
    """
    build_config_panel(general, update_available).render(console)


__all__ = [
    "ConfigView",
    "add_config_row",
    "add_setting_row",
    "build_config_panel",
    "config_view",
    "render_config_panel",
]
