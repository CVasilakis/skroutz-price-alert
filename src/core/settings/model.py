"""Typed, import-light setting declarations and resolution outputs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


def _default_label(key: str) -> str:
    return key.replace("_", " ").title()


def _default_display(value: object) -> str:
    return str(value)


@dataclass(frozen=True, eq=False)
class SettingSpec(Generic[T]):
    """One typed setting declaration.

    Contributors normally provide only ``key``, ``default``, and ``decode``.  The
    compiler validates the declaration and canonicalizes ``default`` through the
    decoder once.  Presentation hooks are optional for settings whose vocabulary
    needs something more specific than the derived label, string display, and
    invalid-value warning.
    """

    key: str
    default: T
    decode: Callable[[object], T]
    label: str | None = None
    display: Callable[[T], str] = field(
        default=_default_display, compare=False, repr=False
    )
    warning: str | None = None
    is_unset: Callable[[object], bool] = field(
        default=lambda value: value is None, compare=False, repr=False
    )

    @property
    def display_label(self) -> str:
        return self.label if self.label is not None else _default_label(self.key)

    @property
    def invalid_warning(self) -> str:
        if self.warning is not None:
            return self.warning
        return f'Unsupported value for "{self.key}"; using {self.display(self.default)}'


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

    @property
    def icon(self) -> str:
        return "🟡" if self.status is SettingStatus.INVALID else "✅"

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

    def __init__(self, pairs: Sequence[tuple[object, ResolvedSetting[Any]]]) -> None:
        self._pairs = tuple(pairs)
        self._values = {spec: resolved for spec, resolved in pairs}

    def __getitem__(self, spec: object) -> Any:
        return self._values[spec].value

    def resolved(self, spec: object) -> ResolvedSetting[Any]:
        return self._values[spec]

    def status(self, spec: object) -> SettingStatus:
        return self._values[spec].status

    def views(self) -> list[SettingView]:
        from core.settings.resolve import setting_view
        return [
            setting_view(cast(Any, spec), resolved)
            for spec, resolved in self._pairs
        ]
