"""Settings data model: resolved values, status codes, the presentation view, and the
per-target resolved-settings accessor.

Pure stdlib dataclasses with no dependency on the rest of the settings package, so this
stays the leaf of the import graph (import-light).

There is deliberately **no** parsed ``settings`` dataclass here: a setting is fully
described by a single :class:`~core.settings.resolve.SettingSpec` (its JSON ``key``,
normalizer, default, display and warning), and resolution reads the raw ``settings``
block by key. The objects below are the *outputs* of resolution.
"""

from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.settings.resolve import SettingSpec


# Resolution status codes for a resolved setting value.
STATUS_OK = "ok"            # config present with a valid, supported value
STATUS_DEFAULT = "default"  # no value set; the spec's default is in effect
STATUS_INVALID = "invalid"  # config sets an unsupported/unparseable value
STATUS_NOCFG = "nocfg"      # the config file is missing entirely


@dataclass
class ResolvedSetting:
    """The effective value of one setting, plus how it was derived.

    The single result type shared by every setting (interval, retention, flag, the
    project-wide reminder settings, and any per-scraper setting). For
    ``execution_interval`` the ``value`` is the canonical interval key (e.g. ``"1h"``);
    for other settings it is the setting's effective value.

    Attributes:
        value: The effective value to apply - the validated user value when OK,
            otherwise the spec's default. Always usable.
        status (str): One of :data:`STATUS_OK`, :data:`STATUS_DEFAULT`,
            :data:`STATUS_INVALID`, :data:`STATUS_NOCFG`; lets callers decide whether
            to warn, footnote, or proceed silently.
        raw: The user's raw value, kept for messages (e.g. the offending input on
            :data:`STATUS_INVALID`); ``None`` unless the status is OK or INVALID.
    """
    value: Any
    status: str
    raw: Any = None


@dataclass
class SettingView:
    """A presentation-ready record of one setting for the settings panel section.

    Built by ``setting_view`` from a ``SettingSpec`` and its :class:`ResolvedSetting`.
    Render sites (the ``--status`` Service Status panel, the interactive Scraping panel,
    and the Configuration Check panel's general settings) map this to their own row/icon
    idiom, so resolution and rendering stay decoupled. The :attr:`icon` / :attr:`is_default`
    helpers centralize the status -> row-decoration decision so the render sites do not
    each re-derive it.

    Attributes:
        label (str): The human-readable setting name (e.g. ``"Execution Interval"``).
        display_value (str): The effective value, formatted for display (e.g. ``"1h"``,
            ``"7 days"``, ``"true"``).
        status (str): The ``STATUS_*`` code (drives the row icon: invalid -> warn).
        footnote (str | None): The invalid-value message when the status is
            :data:`STATUS_INVALID`, otherwise ``None``.
        block_malformed (bool): True when the whole ``settings`` block was ignored
            (present but not an object), so this row fell back to its default *because
            of* that — not because the user simply left it unset. Renders the row as a
            warning (🟡) pointing at the shared block footnote, replacing the old
            standalone "Block ignored" row. Every row shares this flag when it is set,
            since a malformed block defaults them all.
    """
    label: str
    display_value: str
    status: str
    footnote: str | None = None
    block_malformed: bool = False

    @property
    def icon(self) -> str:
        """The status icon: a warning sign for an invalid value (or a malformed block
        that forced this default), else a check."""
        return "🟡" if self.status == STATUS_INVALID or self.block_malformed else "✅"

    @property
    def has_warning(self) -> bool:
        """True when an invalid value should surface its warning footnote.

        The single home for the "this row needs its footnote" decision, so render
        sites query the view instead of each re-deriving ``status == STATUS_INVALID``
        (and importing the constant). Distinct from :attr:`is_default`, which marks an
        unset value that fell back silently.
        """
        return self.status == STATUS_INVALID

    @property
    def is_default(self) -> bool:
        """True when the active value is the spec's default (unset or missing config).

        Distinct from an *invalid* value (which also falls back to the default but is
        flagged with a footnote, not a dim "(default)" marker).
        """
        return self.status not in (STATUS_OK, STATUS_INVALID)

    def render_value(self, note_ref: str = "", *, default_marker: str,
                     value_text: str | None = None, default_note_ref: str = "") -> str:
        """Assembles the row's display value with the shared status decoration.

        The single home for the "invalid → append its footnote reference; unset default →
        append the ``(default)`` marker; valid → the bare value" decision, so every render
        site (the Configuration Check panel, the ``--status`` Service Status panel, and
        the interactive Scraping panel) decorates a settings row identically instead of
        each re-deriving it. Callers pass the pieces that vary by surface:

        Args:
            note_ref (str): The already-formatted footnote reference to append when the
                value is invalid (empty for the other statuses); the caller computes it
                only when :attr:`has_warning`, since registering a footnote has a side
                effect on some panels.
            default_marker (str): The suffix appended for an unset default (e.g.
                ``" [dim](default)[/dim]"``).
            value_text (str | None): The display value already prepared for the surface
                (e.g. Rich-escaped); defaults to :attr:`display_value` when ``None``.
            default_note_ref (str): The already-formatted reference to the shared
                block-malformed footnote, appended after the ``(default)`` marker when
                the block was ignored (see :attr:`block_malformed`); empty otherwise. One
                footnote is shared across all the rows, so the caller registers it once
                and passes the same ref to each.

        Returns:
            str: The decorated value string for the row.
        """
        text = self.display_value if value_text is None else value_text
        if self.has_warning:
            return f"{text}{note_ref}"
        if self.is_default:
            return f"{text}{default_marker}{default_note_ref}"
        return text


class ResolvedSettings:
    """A target's fully-resolved settings, read once and queried by key.

    Built by :func:`core.settings.resolve.resolve_all` from a single config-file read, so
    every consumer (the panel views, the orchestrator's retention/notify gates, a plugin's
    own client/storage via the injected ``self.settings``, and the project-wide reminder
    service) shares one resolution rather than re-reading the file per setting.

    It holds the ordered ``(spec, ResolvedSetting)`` pairs so it can yield both the
    presentation :class:`SettingView` list and typed effective values. It also carries
    an optional :attr:`block_warning` describing a structurally malformed ``settings``
    block (present but not an object). When it is set, every :class:`SettingView` from
    :meth:`views` is flagged :attr:`SettingView.block_malformed`, and the render sites
    register the warning once as a shared footnote that each defaulted row references —
    distinct from a per-setting invalid *value*, which each view footnotes itself.
    For a well-formed block it also records sorted :attr:`unknown_keys`; those values
    are ignored while :attr:`unknown_warning` supplies shared presentation wording.
    """

    def __init__(self, pairs: list[tuple["SettingSpec", ResolvedSetting]],
                 block_warning: str | None = None,
                 unknown_keys: tuple[str, ...] = ()) -> None:
        """Stores the resolved pairs and indexes them by spec key.

        Args:
            pairs: ``(spec, resolved)`` for each of the plugin's settings, in display
                order.
            block_warning: A one-line message when the config's ``settings`` block is
                present but not an object (so it was ignored and every setting fell back
                to its default), else ``None``. Render sites register it once as a shared
                footnote referenced by each defaulted (🟡) setting row.
            unknown_keys: Sorted user keys that no supplied spec declares.
        """
        self._pairs = list(pairs)
        self._by_key = {spec.key: resolved for spec, resolved in self._pairs}
        #: A malformed-``settings``-block message (block present but not an object), or
        #: ``None``. Flags every :meth:`views` row :attr:`SettingView.block_malformed`,
        #: which still show their defaults but as a warning citing the shared footnote.
        self.block_warning = block_warning
        self.unknown_keys = tuple(unknown_keys)

    @property
    def unknown_warning(self) -> str | None:
        """A centrally worded warning for ignored unknown keys, if any."""
        from core.settings.messages import unknown_keys_message
        return unknown_keys_message(self.unknown_keys)

    def get(self, key: str, default: Any = None) -> Any:
        """Returns the effective value for ``key``, or ``default`` if not present.

        The forgiving accessor for plugin code: a key the plugin never declared yields
        ``default`` rather than raising.
        """
        resolved = self._by_key.get(key)
        return resolved.value if resolved is not None else default

    def value(self, key: str) -> Any:
        """Returns the effective value for ``key`` (raises ``KeyError`` if absent).

        The strict accessor for framework code resolving a known built-in setting.
        """
        return self._by_key[key].value

    def status(self, key: str) -> str:
        """Returns the ``STATUS_*`` code for ``key`` (raises ``KeyError`` if absent)."""
        return self._by_key[key].status

    def resolved(self, key: str) -> ResolvedSetting:
        """Returns the full :class:`ResolvedSetting` for ``key``."""
        return self._by_key[key]

    def views(self) -> list["SettingView"]:
        """Returns one :class:`SettingView` per setting, in the plugin's declared order.

        When the ``settings`` block was malformed (:attr:`block_warning` set), every view
        is flagged :attr:`SettingView.block_malformed` so the render sites draw each
        defaulted row as a warning pointing at the shared block footnote, rather than a
        separate "Block ignored" row above them.
        """
        # Imported here (not at module top) to keep this model module the import leaf;
        # setting_view lives with the spec/resolve machinery.
        from core.settings.resolve import setting_view
        malformed = self.block_warning is not None
        return [setting_view(spec, resolved, block_malformed=malformed)
                for spec, resolved in self._pairs]
