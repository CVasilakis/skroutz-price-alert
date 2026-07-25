"""Shared scraper target and declaration naming rules."""

import re

RESERVED_PLUGIN_NAMES = frozenset({"general", "help", "quiet", "ping", "status"})
FRAMEWORK_ITEM_KEYS = frozenset({"id", "name", "target_price", "skip"})
SNAKE_CASE_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")

__all__ = ["FRAMEWORK_ITEM_KEYS", "RESERVED_PLUGIN_NAMES", "SNAKE_CASE_KEY"]
