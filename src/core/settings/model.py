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

    @property
    def entries(self) -> tuple[tuple[Any, ResolvedSetting[Any]], ...]:
        """Return the immutable declaration/resolution entries in declaration order."""
        return self._pairs
