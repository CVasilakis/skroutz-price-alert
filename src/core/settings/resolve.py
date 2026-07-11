"""The generic resolve machinery and the :class:`SettingSpec`.

This is the heart of the settings layer: a single resolver (:func:`resolve_spec`) and a
single result type (:class:`core.settings.model.ResolvedSetting`) serve every setting,
built-in scraper setting, per-scraper setting, or project-wide general setting alike.

A setting is exactly **one** ``SettingSpec``: it owns its JSON ``key``, normalizer,
default, display formatter and invalid-value message. There is no parallel settings
dataclass to subclass and no ``from_dict`` to override - resolution reads the config's
raw ``settings`` block by ``key``. Adding a setting is therefore one spec appended to a
spec list, with no new ``Resolved*`` type, registry passthrough or config-check block.

Import-light: reads the config JSON directly (stdlib ``json``/``os``), never a
transport/parsing library or the storage stack, so it is safe to call from the shell
one-liners, ``--status``, and the reminder service.
"""

import json
import os
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

from core.settings.model import (
    ResolvedSetting, ResolvedSettings, SettingView,
    STATUS_OK, STATUS_DEFAULT, STATUS_INVALID, STATUS_NOCFG,
)


@dataclass(frozen=True)
class SettingSpec:
    """A declarative description of one ``settings`` field - the whole setting.

    One spec fully describes how a setting is read, validated, defaulted and displayed,
    so the generic machinery (:func:`resolve_spec`, :func:`setting_view`) needs no
    per-setting code. The built-in scraper settings live in ``BASE_SETTING_SPECS``; a
    plugin adds its own by returning ``BASE_SETTING_SPECS + [extra]`` from
    ``BasePlugin.get_setting_specs``; the project-wide settings live in
    ``GENERAL_SETTING_SPECS`` - all resolved by this same machinery.

    Attributes:
        key (str): The JSON key this spec reads from the config's ``settings`` block.
            Also the setting's identity (must be unique within a spec list) and the key
            used to look up its resolved value via :class:`ResolvedSettings`.
        label (str): The human-readable name shown in the settings panel.
        normalize (Callable): Maps the raw value to its effective value, or ``None``
            when the value is unsupported (which yields :data:`STATUS_INVALID`).
        display (Callable): Formats an effective value into a display string.
        warning (str): The footnote shown when the value is invalid.
        default (Any): The effective fallback when the value is unset/invalid/missing.
        default_factory (Callable | None): A plugin-aware default, used instead of
            ``default`` when set (e.g. ``execution_interval`` defaults to the plugin's
            own cadence). Receives the owning ``BasePlugin``.
        is_unset (Callable): Predicate for "the user did not set this" (default:
            ``is None``). ``execution_interval`` uses ``not value`` so an empty string
            counts as unset (default) rather than invalid.
    """
    key: str
    label: str
    normalize: Callable[[Any], Any | None]
    display: Callable[[Any], str]
    warning: str
    default: Any = None
    default_factory: Callable[[Any], Any] | None = None
    is_unset: Callable[[Any], bool] = lambda value: value is None

    def default_for(self, plugin: Any = None) -> Any:
        """Returns the effective default, using the plugin-aware factory when present."""
        if self.default_factory is not None:
            return self.default_factory(plugin)
        return self.default


def load_settings_block(config_path: str) -> tuple[Any | None, str | None]:
    """Reads a config file and returns its raw ``settings`` block, once.

    The shared file stage of resolution, factored out so a target's whole settings set
    is read in a single pass (:func:`resolve_all`) rather than re-opening the file per
    setting. Reads the JSON directly (import-light - never the storage stack), so it
    stays safe to call from the shell one-liners and ``--status``.

    Args:
        config_path (str): Absolute path to the JSON config file.

    Returns:
        tuple[Any | None, str | None]: ``(settings_block, None)`` on a clean read
            (``settings_block`` is the raw value of the ``settings`` key, which may be a
            dict, ``None``, or any other type the user wrote); ``(None, STATUS_NOCFG)``
            when the file is missing; ``(None, "readerror")`` when it is unreadable or
            not valid JSON (the corrupt config is surfaced elsewhere - here it degrades
            to the default).
    """
    if not os.path.isfile(config_path):
        return None, STATUS_NOCFG
    try:
        with open(config_path, "r") as file:
            data = json.load(file)
    except (OSError, json.JSONDecodeError):
        return None, "readerror"
    return (data.get("settings") if isinstance(data, dict) else None), None


def resolve_spec(spec: SettingSpec, block: Any, load_status: str | None, plugin: Any = None) -> ResolvedSetting:
    """Resolves one spec against an already-loaded ``settings`` block.

    The single home for the resolve state machine shared by every setting: missing
    config -> NOCFG; unreadable/corrupt -> DEFAULT; a non-dict or absent block, or an
    unset key -> DEFAULT; a value that ``normalize`` rejects (returns ``None``) ->
    INVALID; otherwise OK. Every non-OK branch yields the spec's (plugin-aware) default
    so the caller always has a usable value.

    Args:
        spec (SettingSpec): The setting to resolve.
        block (Any): The raw ``settings`` block from :func:`load_settings_block` (a dict,
            ``None``, or any other type). Coerced to ``{}`` when not a dict.
        load_status (str | None): The load status from :func:`load_settings_block`
            (``None`` on a clean read, ``STATUS_NOCFG``, or ``"readerror"``).
        plugin (Any): The owning plugin, for a spec whose default is plugin-aware.

    Returns:
        ResolvedSetting: The effective value and how it was derived.
    """
    default = spec.default_for(plugin)
    if load_status == STATUS_NOCFG:
        return ResolvedSetting(default, STATUS_NOCFG, None)
    if load_status is not None:
        # A read/parse error degrades to DEFAULT (the corrupt config is surfaced by the
        # storage load path, not here).
        return ResolvedSetting(default, STATUS_DEFAULT, None)

    settings = block if isinstance(block, dict) else {}
    raw = settings.get(spec.key)
    if spec.is_unset(raw):
        return ResolvedSetting(default, STATUS_DEFAULT, None)

    value = spec.normalize(raw)
    if value is None:
        return ResolvedSetting(default, STATUS_INVALID, raw)
    return ResolvedSetting(value, STATUS_OK, raw)


def resolve_one(spec: SettingSpec, config_path: str, plugin: Any = None) -> ResolvedSetting:
    """Resolves a single :class:`SettingSpec` against a config file.

    Reads the config once and folds the spec through :func:`resolve_spec`. Use
    :func:`resolve_all` when several settings of the same target are needed, so the file
    is read only once. Import-light - reads the config JSON directly, never the storage
    class - so it is safe from the shell one-liners and ``--status``.

    Args:
        spec (SettingSpec): The setting to resolve.
        config_path (str): Absolute path to the JSON config file.
        plugin (Any): The owning plugin, for a spec whose default is plugin-aware
            (e.g. ``execution_interval``).

    Returns:
        ResolvedSetting: The effective value and how it was derived.
    """
    block, load_status = load_settings_block(config_path)
    return resolve_spec(spec, block, load_status, plugin)


def resolve_all(specs: list[SettingSpec], config_path: str, plugin: Any = None) -> ResolvedSettings:
    """Resolves every spec against a config file in a single read.

    The single entry point for a target's whole settings set: it reads the config file
    once (:func:`load_settings_block`) and resolves each spec against that one snapshot,
    returning a :class:`ResolvedSettings` accessor that yields both presentation views
    and typed effective values. This is what lets the panel, the orchestrator's gates,
    a plugin's injected ``self.settings``, and the reminder service all share one
    resolution.

    Args:
        specs (list[SettingSpec]): The settings to resolve, in display order.
        config_path (str): Absolute path to the JSON config file.
        plugin (Any): The owning plugin, for plugin-aware defaults.

    Returns:
        ResolvedSettings: The resolved settings, queryable by key and as views.
    """
    block, load_status = load_settings_block(config_path)
    pairs = [(spec, resolve_spec(spec, block, load_status, plugin)) for spec in specs]
    return ResolvedSettings(pairs, block_warning=_block_warning(block, load_status))


def _block_warning(block: Any, load_status: str | None) -> str | None:
    """A one-line warning when the ``settings`` block is present but not an object.

    On a clean read (``load_status`` is ``None``) the block may still be the wrong
    shape — the user wrote ``"settings": "1h"`` or a list/number instead of an object.
    :func:`resolve_spec` silently coerces that to ``{}`` (every setting falls back to its
    default), which would otherwise discard the user's whole settings intent with no
    signal. This returns the additive block-level message the render sites show once;
    ``None`` for an absent, null, or well-formed (dict) block, or any non-clean read
    (missing/corrupt config is surfaced elsewhere).
    """
    if load_status is not None or block is None or isinstance(block, dict):
        return None
    return "The settings section is misformatted; using defaults"


def setting_view(spec: SettingSpec, resolved: ResolvedSetting,
                 block_malformed: bool = False) -> SettingView:
    """Combines a spec and its resolved value into a presentation-ready view.

    Args:
        spec (SettingSpec): The setting's spec (supplies the label, display formatter
            and invalid-value message).
        resolved (ResolvedSetting): The resolved value and status.
        block_malformed (bool): True when the whole ``settings`` block was ignored (not
            an object), so this row's default is a consequence of that; renders as a
            warning pointing at the shared block footnote (see
            :attr:`~core.settings.model.SettingView.block_malformed`).

    Returns:
        SettingView: The row to render in the settings panel section.
    """
    return SettingView(
        label=spec.label,
        display_value=spec.display(resolved.value),
        status=resolved.status,
        footnote=spec.warning if resolved.status == STATUS_INVALID else None,
        block_malformed=block_malformed,
    )
