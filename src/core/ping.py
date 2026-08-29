"""Entry point for the notification check (``./scrooge-alert ping``).

Sends one test payload to every configured Apprise endpoint and reports each
result separately, so a user can tell *which* URL is wrong rather than only that
something is. Reads ``config/general.json`` and touches no scraper, no state, and
no systemd unit.
"""

import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console

from core import messages
from core.application.diagnostics import record_general_diagnostic
from core.constants import CONFIG_DIR
from core.general import load_general_config
from core.infrastructure.logging import setup_global_logging
from core.infrastructure.signals import install_interrupt_handler
from core.notifications.apprise import AppriseNotifier
from core.tui.ping import build_ping_panel


def main():
    """Main entry point for sending a test notification.

    This function initializes the notifier with URLs from general configuration and sends
    a test message, reporting the result of each configured service using Rich.
    """
    install_interrupt_handler()

    setup_global_logging()
    console = Console()
    console.print()

    general = record_general_diagnostic(load_general_config(CONFIG_DIR))
    notifications = general.notifications
    config_error_msg = notifications.error or ""
    if config_error_msg and general.diagnostic_saved is False:
        config_error_msg = f"{config_error_msg} {messages.DIAGNOSTIC_WRITE_FAILED}"
    valid_lookup = set(notifications.valid_urls)
    url_entries = [(url, url in valid_lookup) for url in notifications.configured_urls]

    # Collect and test valid URLs
    valid_urls = [url for url, is_valid in url_entries if is_valid]
    test_results = []
    if valid_urls:
        notifier = AppriseNotifier(valid_urls)
        with console.status("[bold green]Sending test messages...[/bold green]", spinner="dots"):
            test_results = notifier.notify_test()

    # Disable custom signal handling after the update/test phase is complete
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    signal.signal(signal.SIGTERM, signal.SIG_DFL)

    panel, panel_color = build_ping_panel(url_entries, test_results, config_error_msg)
    panel.render(console, panel_color=panel_color)

    console.print()


if __name__ == "__main__":
    main()
