"""Entry point for the health check (``./scrooge-alert status``).

Answers "is this install working?" by collecting five independent things — global
configuration health, each target's configuration and stored state, the software
version against the release branch, whether systemd user lingering lets the timers run
while logged out, and the systemd timer/service state — and handing them to the
presentation-only builders in ``core.tui``.

Dynamic ``--<target>`` flags narrow the per-scraper panels to the named targets,
including an installed-but-unregistered (orphaned) one; the global Configuration
Check panel is target-neutral and always renders.

Strictly read-only: it inspects units without touching them and reads state
without writing it, so running it can never change what a scheduled run will do.
"""

import argparse
import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from core.application.diagnostics import (
    record_general_diagnostic,
    record_target_load_diagnostic,
)
from core.application.preflight import load_target_configs
from core.constants import CONFIG_DIR, STATE_DIR
from core.exceptions import StateFileError
from core.general import load_general_config
from core.infrastructure.logging import setup_global_logging, try_save_diagnostic
from core.infrastructure.signals import install_interrupt_handler
from core.infrastructure.systemd import (
    get_installed_plugin_units,
    get_systemd_properties,
    inspect_user_lingering,
    read_timer_oncalendar,
    scraper_unit_name,
)
from core.infrastructure.updates import SoftwareVersionStatus, inspect_software_version
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.intervals import oncalendar_for
from core.scrapers.framework.naming import RESERVED_PLUGIN_NAMES, SNAKE_CASE_KEY
from core.scrapers.framework.setting_specs import KEY_INTERVAL
from core.scrapers.framework.state import JsonStateRepository
from core.settings import SettingStatus
from core.tui.config_check import config_view, render_config_panel
from core.tui.status import build_not_installed_panel, build_orphan_panel, build_service_panel


def _check_for_updates() -> SoftwareVersionStatus:
    return inspect_software_version()


def _select_targets(catalog: PluginCatalog, orphans: list[str]) -> tuple[list[str], list[str]]:
    """Split the dynamic ``--<target>`` flags into registered and orphaned selections.

    The flag universe is the registered targets plus the installed-but-unregistered
    ones, so an orphaned unit can be inspected on its own. No flag selects everything,
    which keeps the unfiltered report exactly as it was.
    """
    registered = list(catalog.targets)
    parser = argparse.ArgumentParser(description="Scrooge Alert health check")
    for target in registered:
        parser.add_argument(
            f"--{target}",
            action="store_true",
            help=f"Show status for the {catalog.get(target).display_name} scraper",
        )
    # Unit filenames on disk are arbitrary, so only offer a flag for an orphan whose
    # name is a legal target and does not shadow a built-in flag. A skipped name is
    # still reported by the unfiltered run; it just cannot be selected.
    selectable = [
        name
        for name in orphans
        if SNAKE_CASE_KEY.fullmatch(name) and name not in RESERVED_PLUGIN_NAMES
    ]
    for name in selectable:
        parser.add_argument(
            f"--{name}", action="store_true", help=f"Show the orphaned {name} units"
        )

    # Strict parsing: an unknown flag (e.g. a typo'd --<target>) must error out
    # rather than silently widening the report back to every target. status.sh
    # validates its own flags, so this guards direct invocation.
    args = parser.parse_args()

    chosen_registered = [target for target in registered if getattr(args, target)]
    chosen_orphans = [name for name in selectable if getattr(args, name)]
    if not chosen_registered and not chosen_orphans:
        return registered, list(orphans)
    return chosen_registered, chosen_orphans


def main() -> None:
    """Check configuration and systemd status, then render the status report."""
    install_interrupt_handler()
    setup_global_logging()

    catalog = PluginCatalog.discover()
    # One read-only glob feeds both the orphan flags and the orphan report below.
    installed_units = get_installed_plugin_units()
    orphan_names = sorted(name for name in installed_units if name not in set(catalog.targets))
    selected_targets, selected_orphans = _select_targets(catalog, orphan_names)

    console = Console()
    console.print()

    selected_plugins = [catalog.get(target) for target in selected_targets]
    load_results = load_target_configs(selected_plugins, CONFIG_DIR)
    loads_by_target = {load.target: load for load in load_results}
    general = record_general_diagnostic(load_general_config(CONFIG_DIR))
    with console.status("[bold green]Checking for updates...[/bold green]", spinner="dots"):
        version_status = _check_for_updates()
        lingering = inspect_user_lingering()
    render_config_panel(console, general, version_status, lingering)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    for target in selected_targets:
        plugin = catalog.get(target)
        load = loads_by_target.get(target)
        diagnostic_saved = None
        state_failure_detail = None
        if load is not None:
            diagnostic_saved = record_target_load_diagnostic(load)
            if load.failure is None:
                state = JsonStateRepository(
                    os.path.join(STATE_DIR, f"{target}.json"),
                    display_path=f"state/{target}.json",
                )
                try:
                    state.load()
                except StateFileError as exc:
                    state_failure_detail = str(exc)
                    if exc.diagnostic_detail:
                        try_save_diagnostic(exc.diagnostic_detail, target_name=target)

        timer_props = get_systemd_properties(
            scraper_unit_name(target, "timer"), "ActiveState,NextElapseUSecRealtime"
        )
        service_props = get_systemd_properties(
            scraper_unit_name(target, "service"),
            "ActiveState,Result,ExecMainStartTimestamp,ExecMainStatus",
        )

        if not timer_props and not service_props:
            console.print()
            console.print(build_not_installed_panel(target, plugin.display_name))
            continue

        assert load is not None
        resolved = load.settings
        interval_spec = plugin.setting(KEY_INTERVAL)
        interval = resolved.resolved(interval_spec)
        expected_oncalendar = ""
        active_oncalendar = ""
        if interval.status in (SettingStatus.OK, SettingStatus.DEFAULT):
            expected_oncalendar = oncalendar_for(interval.value)
            active_oncalendar = read_timer_oncalendar(target)

        service_panel = build_service_panel(
            target,
            timer_props,
            service_props,
            resolved,
            plugin.config_filename,
            expected_oncalendar,
            active_oncalendar,
            config_view(
                load.count,
                load.faulty_indices,
                load.failure.detail if load.failure is not None else None,
                f"config/{plugin.config_filename}",
                diagnostic_saved,
            ),
            plugin.display_name,
            interval_spec,
            state_failure_detail,
        )
        console.print()
        service_panel.render(console)

    for name in selected_orphans:
        console.print()
        build_orphan_panel(name).render(console)

    console.print()


if __name__ == "__main__":
    main()
