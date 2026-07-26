"""Frontend-independent mapping of resolved settings to redacted display data."""

from __future__ import annotations

from dataclasses import dataclass

from core.settings.model import MISSING, ResolvedSettings, SettingStatus


@dataclass(frozen=True)
class SettingView:
    """Plain display data shared by terminal and logging frontends."""

    label: str
    display_value: str
    status: SettingStatus
    footnote: str | None = None

    @property
    def has_warning(self) -> bool:
        return self.status in (SettingStatus.INVALID, SettingStatus.MISSING)

    @property
    def is_default(self) -> bool:
        return self.status not in (SettingStatus.OK, SettingStatus.INVALID)


def resolved_setting_views(settings: ResolvedSettings) -> tuple[SettingView, ...]:
    """Map immutable resolved entries to redacted, presentation-neutral values."""
    views: list[SettingView] = []
    for spec, resolved in settings.entries:
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
        views.append(
            SettingView(
                label=spec.display_label,
                display_value=display_value,
                status=resolved.status,
                footnote=(
                    spec.invalid_warning
                    if resolved.status in (SettingStatus.INVALID, SettingStatus.MISSING)
                    else None
                ),
            )
        )
    return tuple(views)


__all__ = ["SettingView", "resolved_setting_views"]
