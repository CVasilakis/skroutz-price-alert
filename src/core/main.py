import argparse
import logging
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
from core.application.orchestrator import ScrapingOrchestrator
from core.application.preflight import load_target_configs, validate_notification_preflight
from core.application.reporting import SilentRunReporter
from core.constants import CONFIG_DIR, STATE_DIR
from core.exceptions import UpdateCheckError
from core.exit_status import ExitStatus
from core.general import ReminderService, load_general_config
from core.general.reminder_state import ReminderStateRepository, general_state_path
from core.infrastructure.locking import StateLockManager
from core.infrastructure.logging import save_traceback, setup_global_logging
from core.infrastructure.signals import install_interrupt_handler
from core.infrastructure.updates import check_for_updates
from core.notifications.apprise import AppriseNotifier
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.settings import KEY_RETENTION
from core.tui.config_check import render_config_panel
from core.tui.run_reporter import InteractiveRunReporter


def _run_main() -> None:
    """Run the parsed Scrooge Alert application workflow.

    This function initializes the environment, parses arguments, sets up logging,
    checks for updates, loads targets, and starts the scraping application workflow.
    The workflow delegates each lock/client/state lifecycle to ``TargetRunner``.
    """
    parser = argparse.ArgumentParser(description="Scrooge Alert scraper")
    parser.add_argument("--quiet", action="store_true", help="Run script with no console output")

    # Atomically discover and compile the immutable plugin catalog.
    catalog = PluginCatalog.discover()
    registered_scrapers = list(catalog.targets)
    for scraper in registered_scrapers:
        display_name = catalog.get(scraper).display_name
        parser.add_argument(
            f"--{scraper}", action="store_true", help=f"Run the {display_name} scraper"
        )

    # Strict parsing: an unknown flag (e.g. a typo'd --<plugin>) must error out,
    # not be silently ignored — parse_known_args would fall through to running
    # every scraper. run.sh validates its own flags, so nothing it forwards is
    # unknown here; this guards direct invocation.
    args = parser.parse_args()

    targets_to_run = [s for s in registered_scrapers if getattr(args, s, False)]

    if not targets_to_run:
        targets_to_run = registered_scrapers

    # Single load/validation phase: read each config once into its immutable target load.
    # The orchestrator later reuses these same in-memory snapshots, and the per-target
    # outcomes drive each scraper's 'Config' row and its per-target broken-config skip.
    selected_plugins = [catalog.get(target) for target in targets_to_run]
    load_results = load_target_configs(selected_plugins, CONFIG_DIR)
    general = record_general_diagnostic(load_general_config(CONFIG_DIR))

    if not args.quiet:
        install_interrupt_handler()

        console = Console()
        console.print()

        with console.status("[bold green]Checking for updates...[/bold green]", spinner="dots"):
            try:
                update_available: bool | None = check_for_updates()
            except UpdateCheckError:
                update_available = None
        render_config_panel(console, general, update_available)
        init_fatal_error = None

        # Restore default handlers immediately after the spinner vanishes
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        console.print()

        reporter = InteractiveRunReporter()
    else:
        retention_by_target = {
            load.target: load.settings[load.plugin.setting(KEY_RETENTION)] for load in load_results
        }
        init_fatal_error = validate_notification_preflight(
            targets_to_run,
            general,
            retention_by_target,
        )
        reporter = SilentRunReporter()

    if init_fatal_error:
        for load in load_results:
            record_target_load_diagnostic(load)
        sys.exit(init_fatal_error)

    notifier = AppriseNotifier(general.notifications.valid_urls)

    # Periodic liveness reminder: checked once per invocation (not per scraper), right
    # after the preflight/update-check phase. run_once never raises, and it runs before
    # the orchestrator so an aborted scrape cannot suppress the heartbeat. It logs only to
    # its own file (logs/reminder/), never the console, so it can't break the panel layout
    # of an interactive run.
    reminder_state_path = general_state_path(CONFIG_DIR)
    ReminderService(
        general.settings,
        ReminderStateRepository(reminder_state_path),
        notifier,
        acquire_lock_fn=StateLockManager(os.path.dirname(reminder_state_path)).acquire,
        settings_error=general.settings_error,
    ).run_once()

    client_loader = ClientLoader()
    try:
        orchestrator = ScrapingOrchestrator(
            load_results,
            client_loader,
            notifier,
            args.quiet,
            reporter,
            state_dir=STATE_DIR,
        )
        exit_code = orchestrator.run()

        sys.exit(exit_code)

    except Exception:
        if "reporter" in locals():
            reporter.complete_target()
        save_traceback(logging.root, log_to_console=not args.quiet)
        notifier.notify_crash()
        sys.exit(ExitStatus.APPLICATION_ERROR)


def main() -> None:
    """Run the CLI while preserving quiet startup-failure diagnostics on disk."""
    quiet_requested = "--quiet" in sys.argv[1:]
    setup_global_logging(quiet_requested)
    try:
        _run_main()
    except SystemExit:
        raise
    except Exception:
        save_traceback(logging.root, log_to_console=not quiet_requested)
        sys.exit(ExitStatus.APPLICATION_ERROR)


if __name__ == "__main__":
    main()
