"""Notification endpoint validation and immutable configuration results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum

from core.constants import APPRISE_PLACEHOLDERS

KEY_URLS = "urls"


class NotificationValidationProblem(str, Enum):
    """Stable categories for notification configuration structure failures."""

    NOT_OBJECT = "not_object"
    UNKNOWN = "unknown"
    URLS_NOT_ARRAY = "urls_not_array"
    URL_NOT_STRING = "url_not_string"


class NotificationValidationError(ValueError):
    """A typed notification failure whose text remains backward compatible."""

    def __init__(
        self,
        problem: NotificationValidationProblem,
        *,
        keys: Sequence[str] = (),
        index: int | None = None,
    ) -> None:
        self.problem = problem
        self.keys = tuple(keys)
        self.index = index
        if problem is NotificationValidationProblem.NOT_OBJECT:
            message = "Notifications must be an object"
        elif problem is NotificationValidationProblem.UNKNOWN:
            message = f"Unknown notification settings: {', '.join(self.keys)}"
        elif problem is NotificationValidationProblem.URLS_NOT_ARRAY:
            message = 'Notification setting "urls" must be an array'
        else:
            message = f"Notification URL at JSON index {index} must be a string"
        super().__init__(message)


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
        raise NotificationValidationError(NotificationValidationProblem.NOT_OBJECT)

    unknown = sorted(set(block) - {KEY_URLS})
    if unknown:
        raise NotificationValidationError(
            NotificationValidationProblem.UNKNOWN,
            keys=unknown,
        )

    raw_urls = block.get(KEY_URLS, [])
    if not isinstance(raw_urls, list):
        raise NotificationValidationError(NotificationValidationProblem.URLS_NOT_ARRAY)

    configured: list[str] = []
    for index, raw_url in enumerate(raw_urls, 1):
        if not isinstance(raw_url, str):
            raise NotificationValidationError(
                NotificationValidationProblem.URL_NOT_STRING,
                index=index,
            )
        configured.append(raw_url.strip())

    configured_urls = tuple(configured)
    valid_urls, invalid_urls = classify_notification_urls(configured_urls)
    return NotificationConfig(configured_urls, valid_urls, invalid_urls)


__all__ = [
    "KEY_URLS",
    "NotificationConfig",
    "NotificationValidationError",
    "NotificationValidationProblem",
    "classify_notification_urls",
    "is_valid_apprise_url",
    "resolve_notification_config",
]
