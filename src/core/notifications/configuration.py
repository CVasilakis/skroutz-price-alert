"""Notification endpoint validation and immutable configuration results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from core.constants import APPRISE_PLACEHOLDERS

KEY_URLS = "urls"


def is_valid_apprise_url(url: str) -> bool:
    """Return whether one nonblank, configured URL can be instantiated by Apprise."""
    candidate = (url or "").strip()
    if not candidate or any(placeholder in candidate for placeholder in APPRISE_PLACEHOLDERS):
        return False

    import apprise

    try:
        return bool(apprise.Apprise.instantiate(candidate))
    except Exception:
        return False


def classify_notification_urls(
    notification_urls: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Partition configured URLs into valid and invalid tuples without changing order."""
    valid: list[str] = []
    invalid: list[str] = []
    for raw_url in notification_urls:
        url = raw_url.strip()
        (valid if is_valid_apprise_url(url) else invalid).append(url)
    return tuple(valid), tuple(invalid)


@dataclass(frozen=True)
class NotificationConfig:
    """The redacted, immutable outcome of resolving the notifications section."""

    configured_urls: tuple[str, ...] = field(default=(), repr=False)
    valid_urls: tuple[str, ...] = field(default=(), repr=False)
    invalid_urls: tuple[str, ...] = field(default=(), repr=False)
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None and bool(self.valid_urls)


def resolve_notification_config(block: object | None) -> NotificationConfig:
    """Strictly decode and semantically classify one notifications object."""
    if block is None:
        return NotificationConfig()
    if not isinstance(block, Mapping):
        raise ValueError("Notifications must be an object")

    unknown = set(block) - {KEY_URLS}
    if unknown:
        raise ValueError(f"Unknown notification settings: {', '.join(sorted(unknown))}")

    raw_urls = block.get(KEY_URLS, [])
    if not isinstance(raw_urls, list):
        raise ValueError('Notification setting "urls" must be an array')

    configured: list[str] = []
    for index, raw_url in enumerate(raw_urls, 1):
        if not isinstance(raw_url, str):
            raise ValueError(f"Notification URL at JSON index {index} must be a string")
        configured.append(raw_url.strip())

    configured_urls = tuple(configured)
    valid_urls, invalid_urls = classify_notification_urls(configured_urls)
    return NotificationConfig(configured_urls, valid_urls, invalid_urls)


__all__ = [
    "KEY_URLS",
    "NotificationConfig",
    "classify_notification_urls",
    "is_valid_apprise_url",
    "resolve_notification_config",
]
