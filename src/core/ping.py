import os
import signal
import sys

# Put src/ (the parent of the `core` package) on the path so the absolute
# `core.*` imports below work when this file is invoked directly as a script.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rich.console import Console
from rich.markup import escape

from core.constants import CONFIG_DIR
from core.general import load_general_config
from core.logger import setup_global_logging
from core.notifier import Notifier
from core.ui.panel import StatusPanelBuilder
from core.utils import install_interrupt_handler


def obfuscate_invalid_url(url: str) -> str:
    """Obfuscates an invalid URL for display."""
    schema_end = url.find("://")
    if schema_end != -1:
        scheme = url[: schema_end + 3]
        rest = url[schema_end + 3 :]
    else:
        scheme = ""
        rest = url

    first_slash = rest.find("/")
    if first_slash != -1:
        token = rest[:first_slash]
        path = "/..."
    else:
        token = rest
        path = ""

    if len(token) > 2:
        obfuscated_token = f"{token[0]}...{token[-1]}"
    elif len(token) > 0:
        obfuscated_token = f"{token[0]}..."
    else:
        obfuscated_token = ""

    if not scheme and not obfuscated_token:
        return "***"
    return f"{scheme}{obfuscated_token}{path}"


def build_ping_panel(
    url_entries: list[tuple[str, bool]],
    test_results: list[tuple[str, bool]],
    config_error_msg: str,
) -> tuple[StatusPanelBuilder, str]:
    """Builds the 'Notification Check Results' panel from already-collected inputs.

    Pure presentation: it performs no configuration reading, URL validation or network
    I/O — the caller collects those (configuration order, per-URL validity, and delivery
    results) and hands them in. This mirrors how ``config_check.render_config_panel``
    separates rendering from the ``load_targets`` I/O, and lets the UI test harness drive
    the exact row/footnote/color logic with synthetic inputs.

    Args:
        url_entries (list[tuple[str, bool]]): ``(url, is_valid)`` per configured URL, in
            configuration order.
        test_results (list[tuple[str, bool]]): ``(identifier, success)`` for each *valid*
            URL, in the same relative order (as returned by ``Notifier.notify_test``).
        config_error_msg (str): The general-config error shown when nothing is
            configured, or ``""``.

    Returns:
        tuple[StatusPanelBuilder, str]: The populated panel and its border color
            (``"yellow"`` when results are mixed, ``"green"`` when all succeeded,
            ``"red"`` otherwise).
    """
    panel = StatusPanelBuilder("Notification Check Results")

    # Print results in original configuration order with sequential IDs
    valid_idx = 0
    for idx, (url, is_valid) in enumerate(url_entries, 1):
        prefix = f"Apprise URL {idx}: "
        if not is_valid:
            ref = panel.add_note_ref("Apprise flagged this endpoint as invalid.")
            panel.add_row("❗", "Invalid URL", f"{prefix}{escape(obfuscate_invalid_url(url))}{ref}")
        else:
            identifier, success = test_results[valid_idx]
            valid_idx += 1
            if success:
                panel.add_row("✅", "Notification sent", f"{prefix}{escape(identifier)}")
            else:
                ref = panel.add_note_ref("Failed to deliver the test message.")
                panel.add_row("🛑", "Delivery Failed", f"{prefix}{escape(identifier)}{ref}")

    if not url_entries:
        panel.add_row(
            "🛑",
            "Not Configured",
            f"{config_error_msg or 'No notification URLs found in config/general.json.'}",
        )

    # Custom color logic: yellow when mixed success/error results
    has_success = "✅" in panel.icons
    has_error = "❗" in panel.icons or "🛑" in panel.icons

    if has_success and has_error:
        panel_color = "yellow"
    elif has_success:
        panel_color = "green"
    else:
        panel_color = "red"

    return panel, panel_color


def main():
    """Main entry point for sending a test notification.

    This function initializes the notifier with URLs from general configuration and sends
    a test message, reporting the result of each configured service using Rich.
    """
    install_interrupt_handler()

    setup_global_logging()
    console = Console()
    console.print()

    general = load_general_config(CONFIG_DIR)
    notifications = general.notifications
    config_error_msg = notifications.error or ""
    valid_lookup = set(notifications.valid_urls)
    url_entries = [(url, url in valid_lookup) for url in notifications.configured_urls]

    # Collect and test valid URLs
    valid_urls = [url for url, is_valid in url_entries if is_valid]
    test_results = []
    if valid_urls:
        notifier = Notifier(valid_urls)
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
