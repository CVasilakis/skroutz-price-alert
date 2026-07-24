"""Pure notification-check presentation helpers."""

from rich.markup import escape

from core.tui.panel import StatusPanelBuilder


def obfuscate_invalid_url(url: str) -> str:
    """Obfuscate an invalid URL for display."""
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
    elif token:
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
    """Build the notification-check panel from already-collected inputs."""
    panel = StatusPanelBuilder("Notification Check Results")

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
        detail = (
            config_error_msg
            or "Add notification URLs to `config/general.json`, then retry."
        )
        ref = panel.add_note_ref(detail)
        panel.add_row(
            "🛑",
            "Not Configured",
            f"No URLs{ref}",
        )

    has_success = "✅" in panel.icons
    has_error = "❗" in panel.icons or "🛑" in panel.icons
    if has_success and has_error:
        panel_color = "yellow"
    elif has_success:
        panel_color = "green"
    else:
        panel_color = "red"
    return panel, panel_color


__all__ = ["build_ping_panel", "obfuscate_invalid_url"]
