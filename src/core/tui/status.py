"""Pure status-panel presentation helpers."""

from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from core.presentation import resolved_setting_views
from core.settings import SettingStatus
from core.tui.config_check import (
    ConfigView,
    add_config_row,
    add_setting_row,
)
from core.tui.panel import PANEL_WIDTH, StatusPanelBuilder
from core.tui.service_verdicts import classify_service_state


def build_not_installed_panel(target: str, display_name: str | None = None) -> Panel:
    """Builds the red 'service not installed' panel for a registered-but-unprovisioned plugin.

    Rendered (as a raw ``Panel``, not via ``StatusPanelBuilder``) when neither the timer
    nor the service unit exists on disk for an otherwise-registered plugin.

    Args:
        target (str): The scraper target name.

    Returns:
        Panel: The single-line red panel.
    """
    service_table = Table(show_header=False, box=None, padding=(0, 2))
    service_table.add_column("Icon", justify="center")
    service_table.add_column("Message", style="dim")
    service_table.add_row("❗", "Background service not installed.")
    label = display_name or target.capitalize()
    return Panel(
        service_table,
        title=f"[bold]{escape(label)} Service Status[/bold]",
        border_style="red",
        width=PANEL_WIDTH,
    )


def build_orphan_panel(name: str) -> StatusPanelBuilder:
    """Builds the red 'orphaned unit' panel for an installed unit whose plugin is gone.

    Args:
        name (str): The plugin name parsed from the orphaned unit filename.

    Returns:
        StatusPanelBuilder: The orphan panel (one error row + an uninstall footnote).
    """
    orphan_panel = StatusPanelBuilder(f"{name.capitalize()} Service Status (Orphaned)")
    ref = orphan_panel.add_note_ref(f"Run `./scripts/uninstall.sh --{name}` to remove it")
    orphan_panel.add_row("❗", "Removed Scraper", f"[red]Still scheduled{ref}[/red]")
    return orphan_panel


def build_service_panel(
    target: str,
    timer_props: dict,
    service_props: dict,
    resolved,
    config_filename: str,
    expected_oncalendar: str,
    active_oncalendar: str,
    config: ConfigView,
    display_name: str,
    interval_spec,
    state_failure_detail: str | None = None,
) -> StatusPanelBuilder:
    """Builds the per-plugin Service Status panel from already-collected inputs.

    Pure presentation given the systemd property dicts, the resolved settings, the
    target-configuration health, and the schedule-drift inputs (the caller queries systemd, the
    catalog, the ``load_target_configs`` I/O and the on-disk timer; this only renders). Lets the
    UI test harness drive every config/settings/timer/exit-code variant with synthetic inputs.

    Args:
        target (str): The scraper target name.
        timer_props (dict): Properties of the ``<target>-scraper.timer`` unit
            (``ActiveState``, ``NextElapseUSecRealtime``).
        service_props (dict): Properties of the ``<target>-scraper.service`` unit
            (``ActiveState``, ``Result``, ``ExecMainStartTimestamp``, ``ExecMainStatus``).
        resolved (ResolvedSettings): The target's resolved settings (settings section +
            interval status for the drift gate).
        config_filename (str): The plugin's config filename, for the target-configuration-error note.
        expected_oncalendar (str): The ``OnCalendar`` the configured interval resolves to
            (``""`` when not applicable); compared against ``active_oncalendar`` for drift.
        active_oncalendar (str): The ``OnCalendar`` currently written in the installed
            timer unit (``""`` when none/not applicable).
        config (ConfigView | None): The target's target-configuration health, rendered as the
            leading 'Config' row; ``None`` when unavailable (e.g. missing dependencies).

    Returns:
        StatusPanelBuilder: The populated Service Status panel.
    """
    service_panel = StatusPanelBuilder(f"{display_name} Service Status")

    # Settings section: the target-configuration health ('Config' row) leads, then each scraper's
    # settings (or its active default), then a separator, then the systemd status rows.
    add_config_row(service_panel, config)
    if state_failure_detail:
        ref = service_panel.add_note_ref(state_failure_detail)
        service_panel.add_row("❗", "State", f"[red]Failed[/red]{ref}")
    for view in resolved_setting_views(resolved):
        add_setting_row(service_panel, view)
    service_panel.add_separator()

    timer_active_val = timer_props.get("ActiveState") == "active"
    timer_icon = "✅" if timer_active_val else "❗"
    timer_active = "[green]Yes[/green]" if timer_active_val else "[red]No[/red]"

    result = service_props.get("Result", "")
    exec_status = service_props.get("ExecMainStatus", "")
    last_exec_time = service_props.get("ExecMainStartTimestamp", "")
    service_active = service_props.get("ActiveState", "")

    is_currently_running = service_active in ("active", "activating")

    next_exec = timer_props.get("NextElapseUSecRealtime", "")
    if is_currently_running:
        ref = service_panel.add_note_ref("Script is currently running in the background.")
        next_exec = f"[green]Running Now{ref}[/green]"
        next_exec_icon = "✅"
    elif not next_exec or next_exec in ("n/a", "0"):
        next_exec = "[red]Not Scheduled[/red]"
        next_exec_icon = "❗"
    else:
        next_exec = escape(next_exec)
        next_exec_icon = "✅"

    service_panel.add_row(timer_icon, "Systemd Timer Active", timer_active)

    # A service that has never run yet is a healthy pending state, already conveyed by the
    # Timer Active and Next Scheduled Execution rows, so the Last Execution rows are added
    # only once it has actually executed.
    if last_exec_time:
        # Exit-code presentation lives in one table; status only renders
        # the resolved verdict and links its note as a footnote.
        verdict = classify_service_state(result, exec_status, target, config_filename)
        ref = service_panel.add_note_ref(verdict.note) if verdict.note else ""
        completed_str = f"[{verdict.color}]{verdict.label}{ref}[/{verdict.color}]"
        service_panel.add_row("✅", "Last Execution Time", escape(last_exec_time))
        service_panel.add_row(verdict.icon, "Last Execution Status", completed_str)

    # Flag schedule *drift* only: the live timer's OnCalendar (on disk) versus the
    # effective schedule the configured execution_interval resolves to. An invalid/missing
    # interval is owned by the Execution Interval row above, so the check is gated to a
    # usable (ok/default) interval.
    interval = resolved.resolved(interval_spec)
    if interval.status in (SettingStatus.OK, SettingStatus.DEFAULT):
        if active_oncalendar and active_oncalendar != expected_oncalendar:
            next_exec += service_panel.add_note_ref(
                "Timer differs from config. Run `./scripts/schedule.sh`."
            )

    service_panel.add_row(next_exec_icon, "Next Scheduled Execution", next_exec)
    return service_panel


__all__ = ["build_not_installed_panel", "build_orphan_panel", "build_service_panel"]
