"""Stable, engine-neutral contracts for contributor-authored migrations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]
ConfigMigration: TypeAlias = Callable[[JsonObject], JsonObject]

__all__ = ["ConfigMigration", "JsonObject"]
