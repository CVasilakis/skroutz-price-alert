import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from core.constants import CONFIG_DIR
from core.exit_status import classify_service_state
from core.logger import setup_global_logging
from core.preflight import LoadFailureKind, load_targets
from core.scrapers.intervals import oncalendar_for
from core.scrapers.registry import PluginCatalog
from core.scrapers.settings import KEY_INTERVAL
from core.settings import SettingStatus
from core.systemd_inspection import (
    SYSTEMCTL_QUERY_TIMEOUT_SECONDS as SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
)
from core.systemd_inspection import (
    get_installed_plugin_units,
    get_systemd_properties,
    read_timer_oncalendar,
)
from core.systemd_inspection import (
    get_systemd_user_dir as get_systemd_user_dir,
)
from core.ui.config_check import (
    ConfigView,
    add_config_row,
    add_setting_row,
    config_view,
    render_config_panel,
)
from core.ui.panel import StatusPanelBuilder
from core.utils import install_interrupt_handler


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
        width=75,
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
    orphan_panel.add_row(
        "❗", f"[red]This scraper was removed but is still scheduled.[/red]{ref}", ""
    )
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
    products-config health, and the schedule-drift inputs (the caller queries systemd, the
    catalog, the ``load_targets`` I/O and the on-disk timer; this only renders). Lets the
    UI test harness drive every config/settings/timer/exit-code variant with synthetic inputs.

    Args:
        target (str): The scraper target name.
        timer_props (dict): Properties of the ``<target>-scraper.timer`` unit
            (``ActiveState``, ``NextElapseUSecRealtime``).
        service_props (dict): Properties of the ``<target>-scraper.service`` unit
            (``ActiveState``, ``Result``, ``ExecMainStartTimestamp``, ``ExecMainStatus``).
        resolved (ResolvedSettings): The target's resolved settings (settings section +
            interval status for the drift gate).
        config_filename (str): The plugin's config filename, for the products-error note.
        expected_oncalendar (str): The ``OnCalendar`` the configured interval resolves to
            (``""`` when not applicable); compared against ``active_oncalendar`` for drift.
        active_oncalendar (str): The ``OnCalendar`` currently written in the installed
            timer unit (``""`` when none/not applicable).
        config (ConfigView | None): The target's products-config health, rendered as the
            leading 'Config' row; ``None`` when unavailable (e.g. missing dependencies).

    Returns:
        StatusPanelBuilder: The populated Service Status panel.
    """
    service_panel = StatusPanelBuilder(f"{display_name} Service Status")

    # Settings section: the products-config health ('Config' row) leads, then each scraper's
    # settings (or its active default), then a separator, then the systemd status rows.
    add_config_row(service_panel, config)
    if state_failure_detail:
        ref = service_panel.add_note_ref(state_failure_detail)
        service_panel.add_row("❗", "State", f"[red]Failed[/red]{ref}")
    for view in resolved.views():
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
        # Exit-code presentation lives in one table (exit_status.py); status only renders
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


def main():
    """Main entry point for checking the status of the Scrooge Alert service.

    This function retrieves status information from systemd, validates configuration,
    checks for updates, and prints a formatted status report to the console using rich panels.
    """
    install_interrupt_handler()

    setup_global_logging()
    console = Console()

    # Print a starting empty line
    console.print()

    # Discover targets via the immutable plugin catalog, not by
    # scanning config filenames, so an unrelated JSON file cannot become a target.
    catalog = PluginCatalog.discover()
    registered_scrapers = list(catalog.targets)

    # --- Configuration Checks Panel (global checks only: version + .env) ---
    # load_targets is still the single config-file read; its per-target outcomes now feed
    # the 'Config' row atop each Service Status panel below, not this shared panel.
    load_results = load_targets(list(catalog.plugins), CONFIG_DIR)
    loads_by_target = {tl.target: tl for tl in load_results}
    render_config_panel(console)

    # Disable custom signal handling after the update/test phase is complete
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    # --- Systemd Service Panels ---
    for target in registered_scrapers:
        plugin = catalog.get(target)
        timer_props = get_systemd_properties(
            f"{target}-scraper.timer", "ActiveState,NextElapseUSecRealtime"
        )
        service_props = get_systemd_properties(
            f"{target}-scraper.service", "ActiveState,Result,ExecMainStartTimestamp,ExecMainStatus"
        )

        if not timer_props and not service_props:
            console.print()
            console.print(build_not_installed_panel(target, plugin.display_name))
            continue

        # Reuse the settings resolved by the run's single target-config read for the
        # schedule-drift check below.
        load = loads_by_target[target]
        resolved = load.settings

        # Schedule-drift inputs: only the live timer's OnCalendar (what is on disk) versus
        # the effective schedule the configured execution_interval resolves to. Computed
        # only for a usable (ok/default) interval — an invalid/missing one is owned by the
        # Execution Interval settings row — preserving the original lazy timer-file read.
        interval_spec = plugin.setting(KEY_INTERVAL)
        interval = resolved.resolved(interval_spec)
        expected_oncalendar = ""
        active_oncalendar = ""
        if interval.status in (SettingStatus.OK, SettingStatus.DEFAULT):
            expected_oncalendar = oncalendar_for(interval.value)
            active_oncalendar = read_timer_oncalendar(target)

        config_filename = plugin.config_filename
        service_panel = build_service_panel(
            target,
            timer_props,
            service_props,
            resolved,
            config_filename,
            expected_oncalendar,
            active_oncalendar,
            config_view(
                load.count,
                load.faulty_indices,
                load.failure.detail
                if load.failure is not None and load.failure.kind is LoadFailureKind.CONFIG
                else None,
            ),
            plugin.display_name,
            interval_spec,
            load.failure.detail
            if load.failure is not None and load.failure.kind is LoadFailureKind.STATE
            else None,
        )

        console.print()
        service_panel.render(console)

    # --- Orphaned Unit Panels ---
    # Units installed for a plugin that is no longer registered (removed or
    # renamed in the source tree). They never appear in the loop above (it
    # iterates registered plugins) and can never run - the service's
    # `run.sh --quiet --<plugin>` would be rejected as an unknown flag - so each
    # one is surfaced explicitly, in its own red panel, with removal instructions.
    registered_set = set(registered_scrapers)
    installed_units = get_installed_plugin_units()
    orphans = sorted(name for name in installed_units if name not in registered_set)

    for name in orphans:
        # One panel per orphan: a single error line, with the removal command carried as a
        # footnote (rendered cyan from the backticks). The exact unit filenames are
        # intentionally omitted - the plugin name in the title and the command are enough
        # to identify and remove it. The "❗" icon makes StatusPanelBuilder color the
        # border red on its own (no forced override).
        console.print()
        build_orphan_panel(name).render(console)

    console.print()


if __name__ == "__main__":
    main()
