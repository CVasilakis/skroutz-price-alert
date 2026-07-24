import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from core.application.preflight import (
    LoadFailureKind,
    load_targets,
    record_target_load_diagnostic,
)
from core.constants import CONFIG_DIR
from core.exceptions import UpdateCheckError
from core.general import load_general_config
from core.infrastructure.logging import save_diagnostic, setup_global_logging
from core.infrastructure.signals import install_interrupt_handler
from core.infrastructure.systemd import (
    get_installed_plugin_units,
    get_systemd_properties,
    read_timer_oncalendar,
)
from core.infrastructure.updates import check_for_updates
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.intervals import oncalendar_for
from core.scrapers.framework.settings import KEY_INTERVAL
from core.settings import SettingStatus
from core.tui.config_check import config_view, render_config_panel
from core.tui.status import build_not_installed_panel, build_orphan_panel, build_service_panel


def _check_for_updates() -> bool | None:
    try:
        return check_for_updates()
    except UpdateCheckError:
        return None


def main() -> None:
    """Check configuration and systemd status, then render the status report."""
    install_interrupt_handler()
    setup_global_logging()
    console = Console()
    console.print()

    catalog = PluginCatalog.discover()
    registered_scrapers = list(catalog.targets)
    load_results = load_targets(list(catalog.plugins), CONFIG_DIR)
    loads_by_target = {load.target: load for load in load_results}
    general = load_general_config(CONFIG_DIR)
    if isinstance(general.diagnostic, str) and general.diagnostic.strip():
        try:
            save_diagnostic(general.diagnostic)
        except OSError:
            pass
    with console.status("[bold green]Checking for updates...[/bold green]", spinner="dots"):
        update_available = _check_for_updates()
    render_config_panel(console, general, update_available)

    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    for target in registered_scrapers:
        plugin = catalog.get(target)
        load = loads_by_target.get(target)
        if load is not None:
            record_target_load_diagnostic(load)

        timer_props = get_systemd_properties(
            f"{target}-scraper.timer", "ActiveState,NextElapseUSecRealtime"
        )
        service_props = get_systemd_properties(
            f"{target}-scraper.service",
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
                load.failure.detail
                if load.failure is not None and load.failure.kind is LoadFailureKind.CONFIG
                else None,
                f"config/{plugin.config_filename}",
            ),
            plugin.display_name,
            interval_spec,
            load.failure.detail
            if load.failure is not None and load.failure.kind is LoadFailureKind.STATE
            else None,
        )
        console.print()
        service_panel.render(console)

    registered_set = set(registered_scrapers)
    installed_units = get_installed_plugin_units()
    orphans = sorted(name for name in installed_units if name not in registered_set)
    for name in orphans:
        console.print()
        build_orphan_panel(name).render(console)

    console.print()


if __name__ == "__main__":
    main()
