"""Shared process exit-status vocabulary.

The enum contains only presentation-neutral process semantics. Application priority
and terminal-UI wording remain owned by their respective layers.
"""

from enum import IntEnum


class ExitStatus(IntEnum):
    """Stable process statuses emitted by Scrooge Alert entry points."""

    SUCCESS = 0
    APPLICATION_ERROR = 1
    TARGET_CONFIG_ERROR = 15
    NOTIFICATION_CONFIG_ERROR = 16
    RATE_LIMIT_ERROR = 17
    SCRAPE_ERROR = 18
    STORAGE_ERROR = 19
    NOTIFICATION_ERROR = 20
    PLUGIN_DEPENDENCY_ERROR = 21
    ALREADY_RUNNING = 42
    INTERRUPTED = 130


__all__ = ["ExitStatus"]
