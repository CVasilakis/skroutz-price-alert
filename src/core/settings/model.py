"""Typed, import-light setting declarations and resolution outputs."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import KW_ONLY, dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


class _MissingDefault:
    """Sentinel used when a field or setting has no default."""

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _MissingDefault()


def _default_label(key: str) -> str:
    return key.replace("_", " ").title()


def _default_display(value: object) -> str:
    return str(value)


@dataclass(frozen=True, eq=False)
class SettingSpec(Generic[T]):
    """One typed setting declaration.

    Omitting ``default`` makes a setting required. The compiler validates that an
    optional default is already canonical. Presentation hooks are optional for
    settings whose vocabulary
    needs something more specific than the derived label, string display, and
    invalid-value warning.
    """

    key: str
    decode: Callable[[object], T]
    _: KW_ONLY
    default: T | _MissingDefault = MISSING
    sensitive: bool = False
    label: str | None = None
    display: Callable[[T], str] = field(default=_default_display, compare=False, repr=False)
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
        if self.required:
            return f'Required setting "{self.key}" is missing or invalid'
        return f'Unsupported value for "{self.key}"; using {self.display(cast(T, self.default))}'

    @property
    def required(self) -> bool:
        return self.default is MISSING


class SettingStatus(str, Enum):
    OK = "ok"
    DEFAULT = "default"
    INVALID = "invalid"
    MISSING = "missing"
    NO_CONFIG = "nocfg"


@dataclass(frozen=True)
class ResolvedSetting(Generic[T]):
    value: T
    status: SettingStatus


@dataclass(frozen=True)
class SettingView:
    label: str
    display_value: str
    status: SettingStatus
    footnote: str | None = None

    @property
    def icon(self) -> str:
        return "🟡" if self.status in (SettingStatus.INVALID, SettingStatus.MISSING) else "✅"

    @property
    def has_warning(self) -> bool:
        return self.status in (SettingStatus.INVALID, SettingStatus.MISSING)

    @property
    def is_default(self) -> bool:
        return self.status not in (SettingStatus.OK, SettingStatus.INVALID)

    def render_value(
        self,
        note_ref: str = "",
        *,
        default_marker: str,
        value_text: str | None = None,
        default_note_ref: str = "",
    ) -> str:
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
        value = self._values[spec].value
        if value is MISSING:
            key = getattr(spec, "key", "<unknown>")
            raise RuntimeError(f"Required setting {key!r} was not resolved")
        return value

    def resolved(self, spec: object) -> ResolvedSetting[Any]:
        return self._values[spec]

    def status(self, spec: object) -> SettingStatus:
        return self._values[spec].status

    def views(self) -> list[SettingView]:
        from core.settings.resolve import setting_view

        return [setting_view(cast(Any, spec), resolved) for spec, resolved in self._pairs]
