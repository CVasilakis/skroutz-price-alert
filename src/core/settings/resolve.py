"""Generic typed setting resolution over an already-loaded JSON document."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from core.settings.model import (
    ResolvedSetting, ResolvedSettings, SettingSpec, SettingStatus, SettingView,
)


def resolve_spec(
    spec: SettingSpec[Any], block: Mapping[str, object] | None
) -> ResolvedSetting[Any]:
    if block is None:
        return ResolvedSetting(spec.default, SettingStatus.NO_CONFIG)
    raw = block.get(spec.key)
    if spec.is_unset(raw):
        return ResolvedSetting(spec.default, SettingStatus.DEFAULT)
    try:
        value = spec.decode(raw)
    except (TypeError, ValueError, OverflowError):
        return ResolvedSetting(spec.default, SettingStatus.INVALID)
    return ResolvedSetting(value, SettingStatus.OK)


def resolve_settings(
    specs: Sequence[SettingSpec[Any]], block: Mapping[str, object] | None
) -> ResolvedSettings:
    pairs = [(spec, resolve_spec(spec, block)) for spec in specs]
    return ResolvedSettings(pairs)


def setting_view(spec: SettingSpec[Any], resolved: ResolvedSetting[Any]) -> SettingView:
    return SettingView(
        label=spec.display_label,
        display_value=spec.display(resolved.value),
        status=resolved.status,
        footnote=spec.invalid_warning if resolved.status is SettingStatus.INVALID else None,
    )
