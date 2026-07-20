"""Generic typed setting resolution over an already-loaded JSON document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from core.settings.model import (
    MISSING,
    ResolvedSetting,
    ResolvedSettings,
    SettingSpec,
    SettingStatus,
    SettingView,
)


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
        raise ValueError("settings must be an object")
    known = {spec.key for spec in specs}
    unknown = set(block) - known
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(sorted(unknown))}")
    resolved = resolve_settings(specs, block)
    unresolved = [
        spec.key
        for spec in specs
        if spec.required and resolved.status(spec) in (SettingStatus.MISSING, SettingStatus.INVALID)
    ]
    if unresolved:
        raise ValueError("required settings missing or invalid: " + ", ".join(sorted(unresolved)))
    return resolved


def setting_view(spec: SettingSpec[Any], resolved: ResolvedSetting[Any]) -> SettingView:
    if spec.sensitive:
        display_value = (
            "configured"
            if resolved.value is not MISSING and not spec.is_unset(resolved.value)
            else "not configured"
        )
    elif resolved.value is MISSING:
        display_value = "required"
    else:
        display_value = spec.display(resolved.value)
    return SettingView(
        label=spec.display_label,
        display_value=display_value,
        status=resolved.status,
        footnote=(
            spec.invalid_warning
            if resolved.status in (SettingStatus.INVALID, SettingStatus.MISSING)
            else None
        ),
    )
