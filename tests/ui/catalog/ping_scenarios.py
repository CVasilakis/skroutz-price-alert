"""``--ping`` scenarios: the Notification Check Results panel.

``drive_ping`` feeds ``ping.build_ping_panel`` the .env-ordered ``(url, is_valid)`` entries
and the ``(identifier, success)`` delivery results for the valid ones, exercising the real
row icons (✅ / ❗ / 🛑), the URL obfuscation, and the custom mixed/green/red border logic.
"""

from ui.catalog._base import scenario, Surface
from ui.harness.drivers import drive_ping

# Pre-obfuscated identifiers as Notifier.notify_test would return them. These are
# deliberately fake placeholders (no real token shapes) so the committed snapshots never
# resemble a secret; SLACK_LONG is only long enough to exercise value-cell wrapping.
TGRAM = "tgram://1...n/..."
DISCORD = "discord://9...d/..."
SLACK_LONG = "slack://***@acme-workspace/very-long-obfuscated-notifications-channel/general/..."


@scenario(Surface.PING, "all_delivered", "Two valid URLs, both delivered", tags=("ok",))
def _():
    return drive_ping(
        url_entries=[("tgram://token/123", True), ("discord://id/tok", True)],
        test_results=[(TGRAM, True), (DISCORD, True)],
    )


@scenario(Surface.PING, "mixed_valid_invalid", "One valid+delivered, one invalid URL", tags=("mixed",))
def _():
    return drive_ping(
        url_entries=[("tgram://token/123", True), ("discord://broken", False)],
        test_results=[(TGRAM, True)],
    )


@scenario(Surface.PING, "delivery_failed", "A valid URL whose test delivery failed", tags=("error",))
def _():
    return drive_ping(
        url_entries=[("tgram://token/123", True)],
        test_results=[(TGRAM, False)],
    )


@scenario(Surface.PING, "delivered_and_failed", "One delivered, one failed (mixed)", tags=("mixed",))
def _():
    return drive_ping(
        url_entries=[("tgram://token/123", True), ("discord://id/tok", True)],
        test_results=[(TGRAM, True), (DISCORD, False)],
    )


@scenario(Surface.PING, "invalid_only", "A single invalid URL (obfuscated)", tags=("error",))
def _():
    return drive_ping(
        url_entries=[("tgram://bad-token-value", False)],
        test_results=[],
    )


@scenario(Surface.PING, "not_configured_default", "No URLs configured (default message)", tags=("error",))
def _():
    return drive_ping(url_entries=[], test_results=[], env_error_msg="")


@scenario(Surface.PING, "not_configured_env_error", "No URLs configured (.env error message)", tags=("error",))
def _():
    return drive_ping(url_entries=[], test_results=[], env_error_msg="No .env file found or unreadable")


@scenario(Surface.PING, "long_identifier_wrap", "A delivered URL with a long identifier (wrapping)", tags=("layout",))
def _():
    return drive_ping(
        url_entries=[("slack://***@acme-workspace/general", True)],
        test_results=[(SLACK_LONG, True)],
    )
