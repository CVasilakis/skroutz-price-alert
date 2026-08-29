"""Stable, engine-neutral contracts for contributor-authored migrations.

Both aliases are re-exported through :mod:`core.scrapers.api` so a plugin's
``migrations.py`` can stay import-light. They intentionally describe nothing but
the shape of a transform: the engine owns ordering, version keys, validation, and
the single atomic replacement.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]
"""One decoded JSON document: the object a migration receives and returns."""

ConfigMigration: TypeAlias = Callable[[JsonObject], JsonObject]
"""One consecutive transition, from a single source version to the next.

A plugin exports these from ``migrations.py`` in a ``CONFIG_MIGRATIONS`` dict
keyed by source version, holding exactly one callable for every version from 1 up
to the one before its current ``config_schema_version``.

Each transform must be pure: return a new document (typically
``{**document, ...}``) rather than mutating its input, perform no I/O, import no
private client dependency, inspect no other plugin, and leave both
``schema_version`` and ``plugin_schema_version`` alone -- the engine owns them.
The framework chain runs before the plugin chain, everything is applied in memory,
and the target is validated once at the end.

Example:
    ```python
    def migrate_v1_to_v2(document: JsonObject) -> JsonObject:
        return {**document, "changed_plugin_field": "canonical-value"}

    CONFIG_MIGRATIONS = {1: migrate_v1_to_v2}
    ```
"""

__all__ = ["ConfigMigration", "JsonObject"]
