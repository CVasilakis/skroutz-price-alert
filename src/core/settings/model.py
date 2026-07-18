"""Typed, import-light outputs of settings resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from collections.abc import Sequence
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


class SettingStatus(str, Enum):
    OK = "ok"
    DEFAULT = "default"
    INVALID = "invalid"
    NO_CONFIG = "nocfg"


@dataclass(frozen=True)
class ResolvedSetting(Generic[T]):
    value: T
    status: SettingStatus
    raw: object = None


@dataclass(frozen=True)
class SettingView:
    label: str
    display_value: str
    status: SettingStatus
    footnote: str | None = None
    block_malformed: bool = False

    @property
    def icon(self) -> str:
        return "🟡" if self.status is SettingStatus.INVALID or self.block_malformed else "✅"

    @property
    def has_warning(self) -> bool:
        return self.status is SettingStatus.INVALID

    @property
    def is_default(self) -> bool:
        return self.status not in (SettingStatus.OK, SettingStatus.INVALID)

    def render_value(self, note_ref: str = "", *, default_marker: str,
                     value_text: str | None = None, default_note_ref: str = "") -> str:
        text = self.display_value if value_text is None else value_text
        if self.has_warning:
            return f"{text}{note_ref}"
        if self.is_default:
            return f"{text}{default_marker}{default_note_ref}"
        return text


class ResolvedSettings:
    """An immutable typed lookup keyed by the exact ``SettingSpec`` object."""

    def __init__(self, pairs: Sequence[tuple[object, ResolvedSetting[Any]]],
                 block_warning: str | None = None,
                 unknown_keys: tuple[str, ...] = ()) -> None:
        self._pairs = tuple(pairs)
        self._values = {spec: resolved for spec, resolved in pairs}
        self.block_warning = block_warning
        self.unknown_keys = tuple(unknown_keys)

    def __getitem__(self, spec: object) -> Any:
        return self._values[spec].value

    def resolved(self, spec: object) -> ResolvedSetting[Any]:
        return self._values[spec]

    def status(self, spec: object) -> SettingStatus:
        return self._values[spec].status

    @property
    def unknown_warning(self) -> str | None:
        from core.settings.messages import unknown_keys_message
        return unknown_keys_message(self.unknown_keys)

    def views(self) -> list[SettingView]:
        from core.settings.resolve import setting_view
        return [
            setting_view(cast(Any, spec), resolved,
                         block_malformed=self.block_warning is not None)
            for spec, resolved in self._pairs
        ]
