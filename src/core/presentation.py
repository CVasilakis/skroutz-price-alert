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
        """Whether the configured value was unusable and needs the user's attention."""
        return self.status in (SettingStatus.INVALID, SettingStatus.MISSING)

    @property
    def is_default(self) -> bool:
        """Whether the shown value came from the declaration's default.

        The two ways that happens: the key was absent from a settings block
        (``DEFAULT``), or there was no block to read (``NO_CONFIG``).

        Stated positively so the answer holds on its own rather than only inside
        a caller that tests :attr:`has_warning` first. A required setting has no
        default to fall back to, and an invalid one is reported as a problem
        rather than labelled a default even though it does display one. The
        enumeration also forces any status added later to be classified
        deliberately instead of being absorbed here.
        """
        return self.status in (SettingStatus.DEFAULT, SettingStatus.NO_CONFIG)


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
