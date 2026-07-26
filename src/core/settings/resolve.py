"""Generic typed setting resolution over an already-loaded JSON document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any, cast

from core.settings.model import (
    MISSING,
    ResolvedSetting,
    ResolvedSettings,
    SettingSpec,
    SettingStatus,
)


class SettingsValidationProblem(str, Enum):
    """Stable categories for strict settings-block validation failures."""

    NOT_OBJECT = "not_object"
    UNKNOWN = "unknown"
    REQUIRED = "required"


class SettingsValidationError(ValueError):
    """A typed settings failure whose text remains backward compatible."""

    def __init__(
        self,
        problem: SettingsValidationProblem,
        keys: Sequence[str] = (),
    ) -> None:
        self.problem = problem
        self.keys = tuple(keys)
        if problem is SettingsValidationProblem.NOT_OBJECT:
            message = "settings must be an object"
        elif problem is SettingsValidationProblem.UNKNOWN:
            message = f"unknown settings: {', '.join(self.keys)}"
        else:
            message = f"required settings missing or invalid: {', '.join(self.keys)}"
        super().__init__(message)


def resolve_spec(
    spec: SettingSpec[Any], block: Mapping[str, object] | None
) -> ResolvedSetting[Any]:
    if block is None:
        if spec.required:
            return ResolvedSetting(cast(Any, MISSING), SettingStatus.MISSING)
        return ResolvedSetting(spec.default, SettingStatus.NO_CONFIG)
    raw = block.get(spec.key)
    if spec.is_unset(raw):
        if spec.required:
            return ResolvedSetting(cast(Any, MISSING), SettingStatus.MISSING)
        return ResolvedSetting(spec.default, SettingStatus.DEFAULT)
    try:
        value = spec.decode(raw)
    except (TypeError, ValueError, OverflowError):
        if spec.required:
            return ResolvedSetting(cast(Any, MISSING), SettingStatus.INVALID)
        return ResolvedSetting(spec.default, SettingStatus.INVALID)
    return ResolvedSetting(value, SettingStatus.OK)


def resolve_settings(
    specs: Sequence[SettingSpec[Any]], block: Mapping[str, object] | None
) -> ResolvedSettings:
    pairs = [(spec, resolve_spec(spec, block)) for spec in specs]
    return ResolvedSettings(pairs)


def validate_settings_block(specs: Sequence[SettingSpec[Any]], block: object) -> ResolvedSettings:
    """Validate one strict settings object and resolve all declarations."""
    if not isinstance(block, Mapping):
        raise SettingsValidationError(SettingsValidationProblem.NOT_OBJECT)
    known = {spec.key for spec in specs}
    unknown = sorted(set(block) - known)
    if unknown:
        raise SettingsValidationError(SettingsValidationProblem.UNKNOWN, unknown)
    resolved = resolve_settings(specs, block)
    unresolved = sorted(
        spec.key
        for spec in specs
        if spec.required and resolved.status(spec) in (SettingStatus.MISSING, SettingStatus.INVALID)
    )
    if unresolved:
        raise SettingsValidationError(SettingsValidationProblem.REQUIRED, unresolved)
    return resolved
