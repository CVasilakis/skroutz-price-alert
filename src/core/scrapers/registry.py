import os
import re
import importlib
import inspect
import pkgutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from typing import TYPE_CHECKING

from core import messages
from core.config_constants import GENERAL_CONFIG_FILENAME
from core.exceptions import PluginDiscoveryError, PluginDependencyError
from core.scrapers.base.url import normalize_domain
from core.scrapers.base.settings import (
    SettingSpec, ResolvedSetting, ResolvedSettings,
    resolve_one, resolve_all, oncalendar_for,
    SUPPORTED_INTERVALS, BASE_SETTING_SPECS, KEY_INTERVAL, STATUS_OK,
)

if TYPE_CHECKING:
    from core.scrapers.base.plugin import BasePlugin
    from core.scrapers.base.client import BaseScraperClient
    from core.scrapers.base.storage import BaseDataManager

#: Names the management scripts already claim as built-in '--<name>' flags:
#: --help everywhere, --quiet/--ping/--status in run.sh, --update in install.sh.
#: Those flags are matched before the per-plugin branch in the scripts' argument
#: loops, so a plugin with one of these names would register fine yet its own
#: '--<name>' flag could never reach it.
RESERVED_PLUGIN_NAMES = frozenset({"help", "quiet", "ping", "status", "update"})


@dataclass(frozen=True)
class _PluginMetadata:
    name: str
    display_name: str
    domains: tuple[str, ...]
    config_filename: str
    setting_specs: tuple[SettingSpec, ...]
    default_interval: str
    requirements_path: str | None


def _make_frozen_plugin(original: "BasePlugin", metadata: _PluginMetadata) -> "BasePlugin":
    from core.scrapers.base.plugin import BasePlugin

    class FrozenPlugin(BasePlugin):
        @staticmethod
        def get_name() -> str:
            return metadata.name

        @staticmethod
        def get_display_name() -> str:
            return metadata.display_name

        @staticmethod
        def get_supported_domains() -> list[str]:
            return list(metadata.domains)

        @staticmethod
        def get_config_filename() -> str:
            return metadata.config_filename

        @staticmethod
        def get_client_class() -> 'type[BaseScraperClient]':
            return original.get_client_class()

        @staticmethod
        def get_storage_class() -> 'type[BaseDataManager]':
            return original.get_storage_class()

        def get_setting_specs(self) -> list[SettingSpec]:
            return list(metadata.setting_specs)

        def get_default_interval(self) -> str:
            return metadata.default_interval

        def get_requirements_path(self) -> str | None:
            return metadata.requirements_path

    FrozenPlugin.__name__ = f"Frozen{type(original).__name__}"
    FrozenPlugin.__module__ = type(original).__module__
    return FrozenPlugin()


class ScraperRegistry:
    """Unified registry that replaces ScraperFactory + DataManagerFactory.

    Each plugin is registered as a cohesive unit. The registry can:
    - Discover and register all plugin packages under scrapers/ (idempotent)
    - Resolve a URL to a plugin (using plugin.get_supported_domains())
    - Create client instances (lazy, cached)
    - Create storage/data-manager instances (lazy, cached)
    """
    _plugins: dict[str, 'BasePlugin'] = {}
    _discovered: bool = False

    @classmethod
    def register(cls, plugin: 'BasePlugin', where: str | None = None) -> None:
        """Registers a plugin descriptor after validating its contract.

        The single validation gate: every registration — via :meth:`discover` or a
        direct call — passes the descriptor contract check
        (:meth:`_validate_plugin_contract`) and the incremental domain-overlap check
        (:meth:`_check_domain_conflicts_for`), so a malformed plugin or an
        ambiguously-routed domain fails loudly here no matter how it arrives.

        Args:
            plugin (BasePlugin): The plugin descriptor instance to register.
            where (str | None): A source label for error messages (e.g.
                ``'core.scrapers.skroutz'``). Defaults to the descriptor's module path.

        Raises:
            PluginDiscoveryError: If the descriptor contract is unmet or the plugin
                claims a domain overlapping one already registered.
        """
        from core.scrapers.base.plugin import BasePlugin
        if not isinstance(plugin, BasePlugin):
            raise PluginDiscoveryError(
                f"register() requires a BasePlugin instance, got {type(plugin).__name__}."
            )
        if where is None:
            where = type(plugin).__module__
        frozen = cls._validate_plugin_contract(where, plugin)
        name = frozen.get_name()
        if name in cls._plugins:
            raise PluginDiscoveryError(
                f"Duplicate plugin name '{name}' (from '{where}'): another registered plugin "
                "already uses it. Each plugin's get_name() must be unique."
            )
        cls._check_domain_conflicts_for(frozen)
        filename = frozen.get_config_filename()
        for owner, registered in cls._plugins.items():
            existing = registered.get_config_filename()
            if filename.casefold() == existing.casefold():
                raise PluginDiscoveryError(
                    f"Config filename conflict: plugins '{owner}' and '{name}' both use "
                    f"'{filename}' (filenames are compared case-insensitively)."
                )
        cls._plugins[name] = frozen

    @classmethod
    def _reset(cls) -> None:
        """Clears all registered plugins and the discovery flag. TEST-ONLY.

        Exists so tests can register fake plugins (or exercise the validation gate)
        against a clean registry and restore auto-discovery afterwards — typically in
        a try/finally or fixture. Production code must never call this: the registry
        is deliberately a populate-once process-wide singleton.
        """
        cls._plugins = {}
        cls._discovered = False

    @classmethod
    def discover(cls) -> None:
        """Imports and registers every plugin package under scrapers/ (idempotent).

        Auto-discovery is a no-op after the first successful call, so any entrypoint
        or component may call it freely without worrying about ordering or repeated
        work. The registry's lookup methods call this themselves, so a populated
        registry never depends on a caller remembering to import the package first.

        Each plugin sub-package must expose a module-level ``plugin`` attribute
        (a :class:`BasePlugin` instance) in its ``__init__.py``. A package that
        fails to import, omits ``plugin``, or exposes a non-:class:`BasePlugin`
        value is a programming error in that plugin, so discovery fails loudly with
        a :class:`PluginDiscoveryError` naming the offending package rather than
        silently skipping it.

        Every discovered plugin is registered through :meth:`register` — the single
        validation gate — which checks the lightweight descriptor contract
        (:meth:`_validate_plugin_contract`) and rejects domains overlapping an
        already-registered plugin (:meth:`_check_domain_conflicts_for`). This turns a
        malformed plugin or an ambiguously-routed domain into a loud failure at
        startup rather than a confusing error (or silent misrouting) at first
        scrape. Validation of the
        bound client/storage *classes* is deliberately NOT done here: resolving
        them would trigger each plugin's deferred import of its concrete
        client/storage module (and any heavy transport library it pulls in, e.g.
        ``tls_client`` or ``selenium``), defeating lazy loading for callers that
        only enumerate plugins (argparse flags, ``list_plugins``, ``--status``).
        That check is deferred to first instantiation in :meth:`_resolve_bound_class`.

        Raises:
            PluginDiscoveryError: If a plugin package cannot be imported, does not
                expose a ``plugin`` attribute, exposes a non-BasePlugin value, fails
                the descriptor contract, or claims a domain another plugin handles.
        """
        if cls._discovered:
            return

        from core.scrapers.base.plugin import BasePlugin

        package_dir = Path(__file__).parent
        for _importer, modname, ispkg in pkgutil.iter_modules([str(package_dir)]):
            if not ispkg or modname == "base":
                continue

            try:
                module = importlib.import_module(f"core.scrapers.{modname}")
            except Exception as e:
                raise PluginDiscoveryError(
                    f"Failed to import scraper plugin package 'core.scrapers.{modname}': {e}"
                ) from e

            plugin = getattr(module, "plugin", None)
            if plugin is None:
                raise PluginDiscoveryError(
                    f"Scraper plugin package 'core.scrapers.{modname}' does not expose a "
                    f"module-level 'plugin' attribute. Add `plugin = {modname.capitalize()}Plugin()` "
                    f"to scrapers/{modname}/__init__.py."
                )
            if not isinstance(plugin, BasePlugin):
                raise PluginDiscoveryError(
                    f"The 'plugin' attribute of scraper package 'core.scrapers.{modname}' is "
                    f"a {type(plugin).__name__}, not a BasePlugin instance."
                )
            cls.register(plugin, where=f"core.scrapers.{modname}")

        cls._discovered = True

    @classmethod
    def _validate_plugin_contract(cls, where: str, plugin: 'BasePlugin') -> 'BasePlugin':
        """Validates that a plugin being registered returns usable descriptor values.

        The :class:`BasePlugin` ABC only guarantees the descriptor methods *exist*;
        this additionally checks they return usable values — a non-empty, unique
        name, a non-empty display name and config filename, and a non-empty list of
        string domains. A plugin that fails any check is rejected here (called from
        :meth:`register`, the single validation gate) so the mistake surfaces at
        startup instead of breaking later at first scrape.

        Only the *cheap* descriptor metadata is checked here. The bound
        client/storage classes are intentionally NOT resolved (that would import a
        plugin's transport stack just to enumerate it); their type is validated
        lazily in :meth:`_resolve_bound_class` at first instantiation.

        Args:
            where (str): A source label for error messages (e.g. ``'core.scrapers.skroutz'``).
            plugin (BasePlugin): The plugin descriptor to validate.

        Raises:
            PluginDiscoveryError: If any part of the descriptor contract is unmet.
        """
        from core.scrapers.base.plugin import BasePlugin

        plugin_type = type(plugin)
        if plugin_type.get_timer_directives is not BasePlugin.get_timer_directives:
            raise PluginDiscoveryError(
                f"Plugin '{where}' overrides deprecated get_timer_directives(). Replace it "
                "with get_default_interval(); plugins may no longer provide a string-to-string "
                "mapping, OnCalendar, or custom systemd directives, and the interval must use "
                "the canonical cadences."
            )
        if plugin_type.get_requirements_path is not BasePlugin.get_requirements_path:
            raise PluginDiscoveryError(
                f"Plugin '{where}' overrides deprecated get_requirements_path(). Remove the "
                "override and place dependencies in a requirements.txt beside plugin.py."
            )

        try:
            # Every cheap hook is read exactly once. All later consumers use the wrapper.
            name = plugin.get_name()
            display_name = plugin.get_display_name()
            raw_domains = plugin.get_supported_domains()
            config_filename = plugin.get_config_filename()
            raw_specs = plugin.get_setting_specs()
            default_interval = plugin.get_default_interval()
        except Exception as exc:
            raise PluginDiscoveryError(
                f"Plugin '{where}' failed while reading import-light metadata: {exc}"
            ) from exc
        if not isinstance(name, str) or not name.strip():
            raise PluginDiscoveryError(f"Plugin '{where}' must return a non-empty string from get_name().")
        if not re.fullmatch(r"[A-Za-z0-9_]+", name):
            # The name becomes a '--<name>' CLI flag and a '<name>-scraper'
            # systemd unit. argparse maps a flag's hyphens to underscores in the
            # parsed attribute, so a hyphenated name would never match the
            # registered-target lookup and would silently fall through to running
            # *every* scraper. Reject anything but letters, digits and
            # underscores here so the mistake surfaces at discovery.
            raise PluginDiscoveryError(
                f"Plugin '{where}' returned an invalid get_name() value {name!r}: names must "
                f"contain only letters, digits and underscores (no hyphens, dots or spaces) so "
                f"they map cleanly to '--<name>' CLI flags and '<name>-scraper' systemd units."
            )
        if name in RESERVED_PLUGIN_NAMES:
            raise PluginDiscoveryError(
                f"Plugin '{where}' returned the reserved get_name() value {name!r}: the "
                f"management scripts already use '--{name}' as a built-in flag, so this "
                f"plugin's own '--{name}' flag could never be dispatched. Pick another name."
            )
        if not isinstance(display_name, str) or not display_name.strip():
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}) must return a non-empty string from get_display_name()."
            )

        if isinstance(config_filename, str) and config_filename.casefold() == GENERAL_CONFIG_FILENAME.casefold():
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}) cannot use reserved config filename "
                f"'{config_filename}'; it belongs to project-wide general settings."
            )
        if not isinstance(config_filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*\.json", config_filename
        ):
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}) returned an invalid get_config_filename() value "
                f"{config_filename!r}: it must be a safe JSON basename using only letters, "
                "digits, dots, underscores and hyphens (for example 'my_store.json')."
            )
        if not isinstance(raw_domains, (list, tuple)) or not raw_domains:
            raise PluginDiscoveryError(f"Plugin '{name}' ({where}) must return a non-empty list from get_supported_domains().")
        domains: list[str] = []
        for raw_domain in raw_domains:
            try:
                domain = normalize_domain(raw_domain)
            except ValueError as exc:
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}) returned invalid supported domain "
                    f"{raw_domain!r}: {exc}. Declare hostnames/IPs only."
                ) from exc
            if domain in domains:
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}) declares duplicate normalized domain '{domain}'."
                )
            domains.append(domain)

        # The settings extension point is import-light (pure stdlib spec dataclasses), so
        # validate it here at discovery — unlike the client/storage classes, which are
        # resolved lazily — so a mis-typed settings binding fails loudly at startup rather
        # than at first config read. A setting is fully described by its SettingSpec, so we
        # check the list shape and that every key is a non-empty, unique string (a key is
        # both the JSON field read and the lookup handle for the resolved value, so a blank
        # or duplicated key would silently shadow another setting).
        specs = raw_specs
        if not isinstance(specs, (list, tuple)) or any(not isinstance(spec, SettingSpec) for spec in specs):
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}): get_setting_specs() must return a list of SettingSpec."
            )
        seen_keys: set[str] = set()
        for spec in specs:
            if not isinstance(spec.key, str) or not spec.key.strip():
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): every SettingSpec must declare a non-empty string key."
                )
            if spec.key in seen_keys:
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): duplicate setting key '{spec.key}'. Each setting "
                    f"(built-in or custom) must have a unique key."
                )
            seen_keys.add(spec.key)
            if not isinstance(spec.label, str) or not spec.label.strip():
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): setting '{spec.key}' must have a nonblank string label."
                )
            if not isinstance(spec.warning, str) or not spec.warning.strip():
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): setting '{spec.key}' must have a nonblank string warning."
                )
            for field in ("normalize", "display", "is_unset"):
                if not callable(getattr(spec, field)):
                    raise PluginDiscoveryError(
                        f"Plugin '{name}' ({where}): setting '{spec.key}' field '{field}' must be callable."
                    )
            if spec.default_factory is not None and not callable(spec.default_factory):
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): setting '{spec.key}' default_factory must be callable or None."
                )

        if len(specs) < len(BASE_SETTING_SPECS) or any(
            specs[index] is not base for index, base in enumerate(BASE_SETTING_SPECS)
        ):
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}): get_setting_specs() is missing or replaced the "
                "exact BASE_SETTING_SPECS prefix in its declared order. Extend, don't replace — "
                "return BASE_SETTING_SPECS + [your specs]."
            )

        if not isinstance(default_interval, str) or default_interval not in SUPPORTED_INTERVALS:
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}): get_default_interval() must return one of "
                f"{sorted(SUPPORTED_INTERVALS)} (got {default_interval!r})."
            )

        try:
            source = Path(inspect.getfile(plugin_type)).resolve()
        except (TypeError, OSError) as exc:
            raise PluginDiscoveryError(
                f"Plugin '{name}' ({where}): could not locate plugin.py to discover requirements.txt: {exc}"
            ) from exc
        requirements = source.with_name("requirements.txt")
        metadata = _PluginMetadata(
            name=name,
            display_name=display_name,
            domains=tuple(domains),
            config_filename=config_filename,
            setting_specs=tuple(specs),
            default_interval=default_interval,
            requirements_path=str(requirements) if requirements.is_file() else None,
        )
        frozen = _make_frozen_plugin(plugin, metadata)
        for spec in specs:
            try:
                displayed = spec.display(spec.default_for(frozen))
            except Exception as exc:
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): setting '{spec.key}' default/display failed: {exc}"
                ) from exc
            if not isinstance(displayed, str):
                raise PluginDiscoveryError(
                    f"Plugin '{name}' ({where}): setting '{spec.key}' display(default) must return a string."
                )
        return frozen

    @staticmethod
    def _resolve_bound_class(plugin: 'BasePlugin', getter_name: str, base: type) -> type:
        """Resolves and type-checks a plugin's bound client/storage class on first use.

        Calling the getter triggers the plugin's deferred import of its concrete
        client/storage module — and any heavy transport library it pulls in (e.g.
        ``tls_client`` or ``selenium``). This is done lazily here, at first
        instantiation, rather than during discovery, so merely enumerating plugins
        never loads a scraper's transport stack. The subclass check that used to
        live in discovery moves with it, so a mis-bound class still fails loudly —
        just at the point the store is first used.

        Args:
            plugin (BasePlugin): The plugin whose class binding to resolve.
            getter_name (str): The descriptor method name ('get_client_class' or
                'get_storage_class').
            base (type): The base class the resolved class must subclass.

        Returns:
            type: The validated client/storage class.

        Raises:
            PluginDiscoveryError: If the getter raises or returns a non-subclass.
        """
        name = plugin.get_name()
        try:
            bound_class = getattr(plugin, getter_name)()
        except ImportError as e:
            # The plugin's deferred import pulled in a transport/parsing library
            # that is not installed (its requirements.txt was never installed).
            # Surface a clear, actionable message instead of a raw ModuleNotFoundError.
            missing = getattr(e, "name", None)
            raise PluginDependencyError(messages.plugin_dependency_detail(name, missing)) from e
        except Exception as e:
            raise PluginDiscoveryError(f"Plugin '{name}' failed to provide {getter_name}(): {e}") from e
        if not (isinstance(bound_class, type) and issubclass(bound_class, base)):
            raise PluginDiscoveryError(
                f"Plugin '{name}': {getter_name}() must return a {base.__name__} subclass, got {bound_class!r}."
            )
        return bound_class

    @staticmethod
    def _domains_overlap(d1: str, d2: str) -> bool:
        """Returns True if a single host could match both domains.

        ``BasePlugin.matches_url`` accepts a host that equals a supported domain or
        is a label-boundary subdomain of it, so two domains conflict when they are
        equal or one is a subdomain-suffix of the other (e.g. ``skroutz.gr`` and
        ``shop.skroutz.gr`` both match a host of ``shop.skroutz.gr``).
        """
        if d1 == d2:
            return True
        return d1.endswith("." + d2) or d2.endswith("." + d1)

    @classmethod
    def _check_domain_conflicts_for(cls, plugin: 'BasePlugin') -> None:
        """Ensures a plugin being registered claims no domain another plugin handles.

        ``plugin_for_url`` returns the *first* plugin whose ``matches_url`` accepts a
        URL, iterating in (non-guaranteed) registration order. If two plugins claimed
        the same — or a nesting — domain, routing would be silent and order-dependent.
        Checking each registration against the already-registered set (from
        :meth:`register`, the single validation gate) turns that latent ambiguity
        into a loud failure at startup.

        Args:
            plugin (BasePlugin): The plugin descriptor being registered.

        Raises:
            PluginDiscoveryError: If the plugin claims a domain overlapping one
                already claimed by a different registered plugin.
        """
        name = plugin.get_name()
        for norm in plugin.get_supported_domains():
            for owner, registered in cls._plugins.items():
                if owner == name:
                    continue
                for existing in registered.get_supported_domains():
                    existing_norm = existing
                    if cls._domains_overlap(norm, existing_norm):
                        raise PluginDiscoveryError(
                            f"Domain conflict: plugin '{name}' claims '{norm}', which overlaps with "
                            f"'{existing_norm}' already claimed by plugin '{owner}'. A domain may be "
                            f"handled by only one plugin."
                        )

    @classmethod
    def registered_targets(cls) -> list[str]:
        """Returns a list of all registered plugin target identifiers.

        Returns:
            list[str]: The registered target names.
        """
        cls.discover()
        return list(cls._plugins.keys())

    @classmethod
    def get_plugin(cls, target: str) -> 'BasePlugin':
        """Retrieves a plugin descriptor by its target name.

        Args:
            target (str): The target identifier (e.g. 'skroutz').

        Returns:
            BasePlugin: The plugin descriptor.

        Raises:
            ValueError: If the target is not registered.
        """
        cls.discover()
        if target not in cls._plugins:
            raise ValueError(f"Unsupported plugin: {target}")
        return cls._plugins[target]

    @classmethod
    def get_requirements_path(cls, target: str) -> str | None:
        """Return the registry-computed colocated dependency path for a plugin."""
        return cls.get_plugin(target).get_requirements_path()

    @staticmethod
    def _config_path(plugin: 'BasePlugin', config_dir: str) -> str:
        """Builds the absolute path to a plugin's config file under ``config_dir``.

        The one place ``<config_dir>/<config filename>`` is assembled, shared by every
        settings resolver so the join rule never drifts between them.
        """
        return os.path.join(config_dir, plugin.get_config_filename())

    @staticmethod
    def _spec_for(plugin: 'BasePlugin', key: str) -> SettingSpec:
        """Returns the plugin's :class:`SettingSpec` for ``key`` (raises if absent)."""
        for spec in plugin.get_setting_specs():
            if spec.key == key:
                return spec
        raise KeyError(f"Plugin '{plugin.get_name()}' exposes no setting '{key}'.")

    @classmethod
    def resolve_all_settings(cls, target: str, config_dir: str) -> ResolvedSettings:
        """Resolves every setting a plugin exposes, reading its config file once.

        Iterates the plugin's :meth:`BasePlugin.get_setting_specs` and resolves each
        against ``<config_dir>/<config filename>`` in a single read, returning a
        :class:`ResolvedSettings` accessor that yields both presentation views
        (:meth:`ResolvedSettings.views`) and typed effective values
        (:meth:`ResolvedSettings.value` / :meth:`ResolvedSettings.get`). This is the one
        resolution shared by the settings panel, the orchestrator's retention/notify
        gates, and the ``self.settings`` injected into a plugin's client and storage, so a
        per-scraper setting flows everywhere with no change here. Import-light: reads the
        config JSON directly, without resolving the plugin's storage class.

        Args:
            target (str): The registered target name (e.g. ``'skroutz'``).
            config_dir (str): The directory holding the scrapers' config files.

        Returns:
            ResolvedSettings: The target's resolved settings, queryable by key and as views.
        """
        plugin = cls.get_plugin(target)
        return resolve_all(plugin.get_setting_specs(), cls._config_path(plugin, config_dir), plugin)

    @classmethod
    def resolve_value(cls, target: str, key: str, config_dir: str) -> ResolvedSetting:
        """Resolves a single setting by key for a registered plugin.

        The generic typed accessor: framework code reads a built-in setting by its
        ``KEY_*`` constant (e.g. ``KEY_INTERVAL``), and any caller that needs just one
        value (the shell ``list_interval_status`` bridge, the timer resolver) avoids
        resolving the whole set. Import-light: reads the config JSON directly, without
        resolving the plugin's storage class.

        Args:
            target (str): The registered target name (e.g. ``'skroutz'``).
            key (str): The setting's key (e.g. ``KEY_INTERVAL``).
            config_dir (str): The directory holding the scrapers' config files.

        Returns:
            ResolvedSetting: The effective value and how it was derived.

        Raises:
            KeyError: If the plugin exposes no setting with that key.
        """
        plugin = cls.get_plugin(target)
        spec = cls._spec_for(plugin, key)
        return resolve_one(spec, cls._config_path(plugin, config_dir), plugin)

    @staticmethod
    def timer_directives_for(plugin: 'BasePlugin', interval: ResolvedSetting) -> dict[str, str]:
        """Translate an already-resolved interval to framework-owned timer metadata.

        The single boundary where the settings layer's user-facing vocabulary becomes a
        systemd schedule. When the interval is unset/invalid, the plugin's validated
        canonical default is used. Takes the resolved interval
        rather than reading the config, so a caller that already holds it (``--status``,
        which consumes this directly) reuses its one read instead of re-resolving.
        """
        canonical = interval.value if interval.status == STATUS_OK else plugin.get_default_interval()
        return {"OnCalendar": oncalendar_for(canonical)}

    @classmethod
    def resolve_timer_directives(cls, target: str, config_dir: str) -> dict[str, str]:
        """The plugin's ``[Timer]`` directives with ``OnCalendar`` resolved from config.

        Reads the target's ``execution_interval`` and folds it through
        :meth:`timer_directives_for` (the canonical-key -> systemd translation). This is
        the single source of truth for a plugin's *effective* cadence, consumed by
        ``install.sh`` and ``schedule.sh`` through the shell one-liners.

        Args:
            target (str): The registered target name.
            config_dir (str): The directory holding the scrapers' config files.

        Returns:
            dict[str, str]: The effective ``[Timer]`` trigger directives.
        """
        interval = cls.resolve_value(target, KEY_INTERVAL, config_dir)
        return cls.timer_directives_for(cls.get_plugin(target), interval)

    @classmethod
    def plugin_for_url(cls, url: str) -> "BasePlugin | None":
        """Resolves a URL to its registered plugin, or None if no plugin matches.

        A class-level lookup that needs no registry instance (and no config dir):
        it is the single place the supported-domain match is performed, used both
        by ``resolve_target`` and by components such as the notifier that only need
        a plugin's metadata (e.g. its display name) for a given product URL.

        Args:
            url (str): The product URL.

        Returns:
            BasePlugin | None: The matching plugin, or None when unsupported.
        """
        cls.discover()
        for plugin in cls._plugins.values():
            if plugin.matches_url(url):
                return plugin
        return None

    def __init__(self, config_dir: str):
        """Initializes the ScraperRegistry with a configuration directory.

        Args:
            config_dir (str): The directory containing configuration files.
        """
        self._scrapers: dict[str, 'BaseScraperClient'] = {}
        self._managers: dict[str, 'BaseDataManager'] = {}
        self._settings: dict[str, ResolvedSettings] = {}
        self.config_dir = config_dir

    def settings_for(self, target: str) -> ResolvedSettings:
        """Returns the target's resolved settings, resolved once per run and cached.

        The per-run resolved-settings accessor: the client and the data manager are both
        injected with this same object, and the orchestrator reads its retention/notify
        gates from it, so a target's config file is read once for the whole run regardless
        of how many of its settings (built-in or custom) are consulted. Stateless callers
        with no registry instance (``--status``, the shell one-liners) use the
        :meth:`resolve_all_settings` classmethod instead.
        """
        if target not in self._settings:
            self._settings[target] = self.resolve_all_settings(target, self.config_dir)
        return self._settings[target]

    def resolve_target(self, url: str) -> str:
        """Determines the scraper target based on the URL domain.

        Args:
            url (str): The product URL.

        Returns:
            str: The identifier for the scraper target (e.g. 'skroutz').

        Raises:
            ValueError: If the URL belongs to an unsupported domain.
        """
        plugin = self.plugin_for_url(url)
        if plugin is None:
            raise ValueError(f"Unsupported domain: {urlparse(url).netloc.lower()}")
        return plugin.get_name()

    def get_scraper(self, url: str) -> 'BaseScraperClient':
        """Retrieves or creates an appropriate scraper client for the given URL.

        Args:
            url (str): The product URL to determine the correct scraper for.

        Returns:
            BaseScraperClient: The instantiated scraper client.

        Raises:
            ValueError: If the URL belongs to an unsupported domain.
        """
        target = self.resolve_target(url)

        if target not in self._scrapers:
            from core.scrapers.base.client import BaseScraperClient
            plugin = self._plugins[target]
            client_cls = self._resolve_bound_class(plugin, "get_client_class", BaseScraperClient)
            # Pass the target's resolved settings at construction (mirroring the data
            # manager) so a store-specific knob declared in the plugin's
            # get_setting_specs is readable from __init__ onward via self.settings.
            self._scrapers[target] = client_cls(settings=self.settings_for(target))

        return self._scrapers[target]

    def get_manager(self, target: str) -> 'BaseDataManager':
        """Retrieves or creates an appropriate data manager for the given target.

        Args:
            target (str): The target identifier (e.g. 'skroutz').

        Returns:
            BaseDataManager: The instantiated data manager.

        Raises:
            ValueError: If the target is unsupported.
        """
        if target not in self._managers:
            plugin = self.get_plugin(target)

            from core.scrapers.base.storage import BaseDataManager
            storage_cls = self._resolve_bound_class(plugin, "get_storage_class", BaseDataManager)
            path = self._config_path(plugin, self.config_dir)
            # Inject the plugin so the manager resolves supported domains through it (the
            # single source of truth) instead of importing a concrete plugin, and the
            # target's resolved settings so a store-specific setting is readable at scrape
            # time via self.settings.
            self._managers[target] = storage_cls(path, plugin, self.settings_for(target))

        return self._managers[target]

    def close_all(self) -> None:
        """Closes all cached scraper clients."""
        for scraper in self._scrapers.values():
            scraper.close()
