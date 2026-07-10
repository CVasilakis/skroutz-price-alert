import argparse
import sys
import os
import logging
import signal

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.constants import CONFIG_DIR, EXIT_CODE_ERROR
from core.utils import install_interrupt_handler
from core.scrapers.registry import ScraperRegistry
from core.notifier import Notifier
from core.logger import setup_global_logging, save_traceback
from core.orchestrator import ScrapingOrchestrator
from core.ui.tui import InteractiveExecutionStrategy, SilentExecutionStrategy
from core.preflight import load_targets
from core.ui.config_check import preflight
from core.general import ReminderService

from rich.console import Console

def main() -> None:
    """Main entry point for the Scrooge Alert application.

    This function initializes the environment, parses arguments, sets up logging,
    checks for updates, loads products, and starts the scraping orchestrator.
    It delegates file locking and scraping execution to the ScrapingOrchestrator.
    """
    parser = argparse.ArgumentParser(description='Scrooge Alert scraper')
    parser.add_argument('--quiet', action='store_true', help='Run script with no console output')

    # Discover and register all scraper plugins (idempotent).
    registered_scrapers = ScraperRegistry.registered_targets()
    for scraper in registered_scrapers:
        parser.add_argument(f'--{scraper}', action='store_true', help=f'Run the {scraper.capitalize()} scraper')

    # Strict parsing: an unknown flag (e.g. a typo'd --<plugin>) must error out,
    # not be silently ignored — parse_known_args would fall through to running
    # every scraper. run.sh validates its own flags, so nothing it forwards is
    # unknown here; this guards direct invocation.
    args = parser.parse_args()

    setup_global_logging(args.quiet)

    registry = ScraperRegistry(CONFIG_DIR)
    targets_to_run = [s for s in registered_scrapers if getattr(args, s, False)]

    if not targets_to_run:
        targets_to_run = registered_scrapers

    # Single load/validation phase: read each config once into its cached manager.
    # The orchestrator later reuses these same in-memory snapshots, and the per-target
    # outcomes drive each scraper's 'Config' row and its per-target broken-config skip.
    load_results = load_targets(registry, targets_to_run)
    loads_by_target = {tl.target: tl for tl in load_results}

    if not args.quiet:
        install_interrupt_handler()

        console = Console()
        console.print()

        init_fatal_error = preflight(console, targets_to_run, quiet=False)

        # Restore default handlers immediately after the spinner vanishes
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        signal.signal(signal.SIGTERM, signal.SIG_DFL)

        console.print()

        ui_strategy = InteractiveExecutionStrategy()
    else:
        init_fatal_error = preflight(None, targets_to_run, quiet=True)
        ui_strategy = SilentExecutionStrategy()

    if init_fatal_error:
        sys.exit(init_fatal_error)

    notification_urls = os.environ.get("NOTIFICATION_URLS", "")
    notifier = Notifier(notification_urls)

    # Periodic liveness reminder: checked once per invocation (not per scraper), right
    # after the preflight/update-check phase. run_once never raises, and it runs before
    # the orchestrator so an aborted scrape cannot suppress the heartbeat. It logs only to
    # its own file (logs/reminder/), never the console, so it can't break the panel layout
    # of an interactive run.
    ReminderService(CONFIG_DIR, notifier).run_once()

    try:
        try:
            orchestrator = ScrapingOrchestrator(targets_to_run, registry, notifier, CONFIG_DIR, args.quiet, ui_strategy, loads_by_target)
            exit_code = orchestrator.run()
        finally:
            registry.close_all()

        sys.exit(exit_code)

    except Exception:
        if 'ui_strategy' in locals():
            ui_strategy.complete_target()
        save_traceback(logging.root)
        notifier.notify_crash()
        sys.exit(EXIT_CODE_ERROR)

if __name__ == "__main__":
    main()
