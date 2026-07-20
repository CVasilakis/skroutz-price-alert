import argparse
import logging
import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from core.constants import CONFIG_DIR, EXIT_CODE_ERROR
from core.general import ReminderService, load_general_config
from core.general.reminder import general_state_path
from core.logger import save_traceback, setup_global_logging
from core.notifier import Notifier
from core.orchestrator import ScrapingOrchestrator
from core.preflight import load_targets
from core.reporting import SilentRunReporter
from core.scrapers.registry import ClientLoader, PluginCatalog
from core.scrapers.settings import KEY_RETENTION
from core.ui.config_check import preflight
from core.ui.tui import InteractiveRunReporter
from core.utils import install_interrupt_handler


def main() -> None:
    """Main entry point for the Scrooge Alert application.

    This function initializes the environment, parses arguments, sets up logging,
    checks for updates, loads products, and starts the scraping orchestrator.
    It delegates file locking and scraping execution to the ScrapingOrchestrator.
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

    setup_global_logging(args.quiet)

    targets_to_run = [s for s in registered_scrapers if getattr(args, s, False)]

    if not targets_to_run:
        targets_to_run = registered_scrapers

    # Single load/validation phase: read each config once into its immutable target load.
    # The orchestrator later reuses these same in-memory snapshots, and the per-target
    # outcomes drive each scraper's 'Config' row and its per-target broken-config skip.
    selected_plugins = [catalog.get(target) for target in targets_to_run]
    load_results = load_targets(selected_plugins, CONFIG_DIR)
    general = load_general_config(CONFIG_DIR)

    if not args.quiet:
        install_interrupt_handler()

        console = Console()
        console.print()

        init_fatal_error = preflight(console, targets_to_run, quiet=False, general=general)

        # Restore default handlers immediately after the spinner vanishes
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        console.print()

        reporter = InteractiveRunReporter()
    else:
        retention_by_target = {
            load.target: load.settings[load.plugin.setting(KEY_RETENTION)] for load in load_results
        }
        init_fatal_error = preflight(
            None,
            targets_to_run,
            quiet=True,
            general=general,
            retention_by_target=retention_by_target,
        )
        reporter = SilentRunReporter()

    if init_fatal_error:
        sys.exit(init_fatal_error)

    notifier = Notifier(general.notifications.valid_urls)

    # Periodic liveness reminder: checked once per invocation (not per scraper), right
    # after the preflight/update-check phase. run_once never raises, and it runs before
    # the orchestrator so an aborted scrape cannot suppress the heartbeat. It logs only to
    # its own file (logs/reminder/), never the console, so it can't break the panel layout
    # of an interactive run.
    ReminderService(
        general.settings,
        general_state_path(CONFIG_DIR),
        notifier,
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
        )
        exit_code = orchestrator.run()

        sys.exit(exit_code)

    except Exception:
        if "reporter" in locals():
            reporter.complete_target()
        save_traceback(logging.root)
        notifier.notify_crash()
        sys.exit(EXIT_CODE_ERROR)


if __name__ == "__main__":
    main()
