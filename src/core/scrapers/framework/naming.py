"""Shared scraper target and declaration naming rules."""

import re

SHELL_RESERVED_PLUGIN_NAMES = frozenset({"debug", "help", "quiet"})
INTERNAL_RESERVED_PLUGIN_NAMES = frozenset({"general", "migration", "reminder"})
RESERVED_PLUGIN_NAMES = SHELL_RESERVED_PLUGIN_NAMES | INTERNAL_RESERVED_PLUGIN_NAMES
FRAMEWORK_ITEM_KEYS = frozenset({"id", "name", "target_price", "skip"})
SNAKE_CASE_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")

__all__ = [
    "FRAMEWORK_ITEM_KEYS",
    "INTERNAL_RESERVED_PLUGIN_NAMES",
    "RESERVED_PLUGIN_NAMES",
    "SHELL_RESERVED_PLUGIN_NAMES",
    "SNAKE_CASE_KEY",
]
