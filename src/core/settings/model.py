"""Typed, import-light setting declarations and resolution outputs.

The engine is generic and serves both ``config/general.json`` and each target's
``settings`` block. :class:`SettingSpec` and :class:`ResolvedSettings` are also
the plugin-facing halves: a descriptor declares specs (re-exported through
:mod:`core.scrapers.api`) and its client reads resolved values with
``self.settings[SPEC]``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import KW_ONLY, dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar, cast

T = TypeVar("T")


class _MissingDefault:
    """Sentinel used when a field or setting has no default.

    A dedicated sentinel rather than ``None``, because ``None`` is a legitimate
    default for an optional declaration; only identity with :data:`MISSING`
    means "required".
    """

    def __repr__(self) -> str:
        return "MISSING"


MISSING = _MissingDefault()
"""The sole sentinel instance. Compare with ``is``, never by equality."""


def _default_label(key: str) -> str:
    """Derive a panel label from a key: ``min_price`` becomes ``Min Price``."""
    return key.replace("_", " ").title()


def _default_display(value: object) -> str:
    """Render a resolved value for panels when a declaration provides no display."""
    return str(value)


@dataclass(frozen=True, eq=False)
class SettingSpec(Generic[T]):
    """One typed setting declaration.

    Omitting ``default`` makes a setting required. The compiler validates that an
    optional default is already canonical. The presentation hooks
    (:attr:`label`, :attr:`display`, :attr:`warning`, :attr:`is_unset`) are
    optional, and only needed by a setting whose vocabulary wants something more
    specific than the derived label, string display, and invalid-value warning.

    Declare each spec once at module scope and keep that object: like an item
    field, ``eq=False`` gives declarations identity semantics, so
    ``self.settings[SPEC]`` is an exact typed lookup rather than a lookup by
    name. A plugin declares its specs in :attr:`~core.scrapers.api.ScraperPlugin.settings`;
    the framework prepends its own (``execution_interval``,
    ``log_retention_days``, ``notify_scraping_errors``,
    ``suppress_repeated_price_alerts``).

    Example:
        ```python
        MIN_PRICE = SettingSpec[float](
            key="min_price",
            decode=decode_nonnegative_float,
            default=0.0,
            display=lambda value: f"{value:g} EUR" if value else "disabled",
        )
        API_TOKEN = SettingSpec[str](
            key="api_token", decode=decode_nonblank, sensitive=True
        )  # required
        ```
    """

    key: str
    """The snake_case key users write inside the ``settings`` object.

    Must be unique across the framework's settings and this plugin's own; an
    unknown key in a user's ``settings`` block is a fatal configuration error for
    that target, so the name is part of the plugin's public config contract.
    """

    decode: Callable[[object], T]
    """Turn one raw JSON value into this setting's canonical Python value.

    Returns the canonical value or raises :class:`TypeError`,
    :class:`ValueError`, or :class:`OverflowError`. What a rejection costs
    depends on :attr:`default`: an optional setting falls back to its default and
    shows :attr:`invalid_warning`, while a required one fails that target's
    entire configuration.
    """

    _: KW_ONLY

    default: T | _MissingDefault = MISSING
    """The value used when the key is unset or invalid; omit it to require the setting.

    An optional default must already be canonical (``decode(default) ==
    default``), which compilation enforces, so a fallback never needs decoding
    and can be displayed as-is.
    """

    sensitive: bool = False
    """Whether the value is a credential that must never be rendered.

    Sensitive settings resolve normally for the client but are reduced to
    "configured" / "not configured" in every panel and diagnostic, and their
    default is exempt from display validation. Set it for tokens, keys, and
    cookies.
    """

    label: str | None = None
    """An explicit panel label; ``None`` derives one from :attr:`key`."""

    display: Callable[[T], str] = field(default=_default_display, compare=False, repr=False)
    """Render one resolved value for panels; defaults to ``str``.

    Use it when the raw value reads poorly to a user -- units, a pluralized
    count, or a "disabled" wording for a neutral value. It receives only already
    canonical values (a decoded one or the default), must return a
    control-character-free ``str``, and is never called for a sensitive setting.
    """

    warning: str | None = None
    """Custom plain text shown when the configured value is unusable.

    ``None`` derives the wording from the key and default. Plain text only:
    paired backticks may mark a command or path, Rich tags are displayed
    literally, and control characters are rejected at compile time. Length is
    unbounded -- the TUI wraps -- so do not add manual wrapping or indentation.
    """

    is_unset: Callable[[object], bool] = field(
        default=lambda value: value is None, compare=False, repr=False
    )
    """Decide whether a raw value means "not configured" rather than "invalid".

    An unset optional setting takes its default silently; an invalid one takes
    the default *and* warns. The default predicate treats only JSON ``null`` as
    unset. Widen it when a store's vocabulary has another neutral form -- the
    framework's ``execution_interval`` also accepts an empty string.
    """

    @property
    def display_label(self) -> str:
        """The panel label: :attr:`label` when given, otherwise derived from the key."""
        return self.label if self.label is not None else _default_label(self.key)

    @property
    def invalid_warning(self) -> str:
        """The message shown when a configured value could not be used."""
        if self.warning is not None:
            return self.warning
        if self.required:
            return f'Required setting "{self.key}" is missing or invalid'
        return f'Unsupported value for "{self.key}"; using {self.display(cast(T, self.default))}'

    @property
    def required(self) -> bool:
        """Whether the target's configuration must supply a usable value."""
        return self.default is MISSING


class SettingStatus(str, Enum):
    """How one declaration's value was obtained, for presentation and warnings."""

    OK = "ok"
    """The configured value decoded successfully."""

    DEFAULT = "default"
    """A settings block existed but left this key unset; the default applies."""

    INVALID = "invalid"
    """A value was configured but rejected; an optional setting fell back to its default."""

    MISSING = "missing"
    """A required setting had no usable value, so no value is available at all."""

    NO_CONFIG = "nocfg"
    """No settings block existed to read, distinguished from an explicit omission."""


@dataclass(frozen=True)
class ResolvedSetting(Generic[T]):
    """One declaration's resolved value together with where it came from."""

    value: T
    """The canonical value: decoded, or the declaration's default. :data:`MISSING`
    for an unresolved required setting."""

    status: SettingStatus
    """The provenance of :attr:`value`, used for panel wording and warnings."""


class ResolvedSettings:
    """An immutable typed lookup keyed by the exact ``SettingSpec`` object.

    What a client receives as ``self.settings``. It holds every declaration for
    one target -- the framework's and the plugin's -- in declaration order.
    """

    def __init__(self, pairs: Sequence[tuple[object, ResolvedSetting[Any]]]) -> None:
        self._pairs = tuple(pairs)
        self._values = {spec: resolved for spec, resolved in pairs}

    def __getitem__(self, spec: object) -> Any:
        """Return the usable value for one declaration object.

        Args:
            spec: The very :class:`SettingSpec` object that was declared; lookup
                is by identity, so an equivalent-looking copy raises
                :class:`KeyError`.

        Returns:
            The decoded value, or the declaration's default when the key was
            unset or invalid. Typed as ``Any`` because one container holds
            heterogeneous declarations; it is the spec's ``T`` at runtime.

        Raises:
            KeyError: The declaration does not belong to this target.
            RuntimeError: A required setting was never resolved. Unreachable
                through normal loading, which fails the target's configuration
                first, so it guards direct construction in tests and tooling.
        """
        value = self._values[spec].value
        if value is MISSING:
            key = getattr(spec, "key", "<unknown>")
            raise RuntimeError(f"Required setting {key!r} was not resolved")
        return value

    def resolved(self, spec: object) -> ResolvedSetting[Any]:
        """Return the value and its :class:`SettingStatus` without the MISSING guard."""
        return self._values[spec]

    def status(self, spec: object) -> SettingStatus:
        """Return only how one declaration's value was obtained."""
        return self._values[spec].status

    @property
    def entries(self) -> tuple[tuple[Any, ResolvedSetting[Any]], ...]:
        """Return the immutable declaration/resolution entries in declaration order."""
        return self._pairs
