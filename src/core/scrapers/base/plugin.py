import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from urllib.parse import urlparse
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.base.settings import SettingSpec, BASE_SETTING_SPECS


class BasePlugin(ABC):
    """Descriptor that binds a scraper's client, storage, model, and metadata
    into a single cohesive unit.

    One plugin = one scraper target. The plugin is the single source of truth
    for domain lists, config filenames, display names, and class bindings.
    This prevents drift between components (e.g. a client supporting domains
    that its storage does not recognize).

    Import-light contract (load-bearing — do not break):
        The descriptor module (``plugin.py``) and the package ``__init__`` are
        imported for *every* plugin during discovery, merely to enumerate the
        available scrapers (argparse flags, ``list_plugins``, ``--status``,
        ``install.sh``). They must therefore import only stdlib and the base
        contracts — never a transport/parsing library (``tls_client``,
        ``selenium``, ``lxml``, ...). Those belong behind the deferred imports in
        :meth:`get_client_class` / :meth:`get_storage_class`, which run only when a
        scrape actually instantiates the bound class. This is what lets a plugin
        ship its dependencies in its own ``requirements.txt`` (see
        :meth:`get_requirements_path`) and stay uninstalled without breaking
        discovery for every other command.
    """

    @staticmethod
    @abstractmethod
    def get_name() -> str:
        """Returns a unique machine-readable identifier (e.g. 'skroutz', 'amazon')."""
        ...

    @staticmethod
    @abstractmethod
    def get_display_name() -> str:
        """Returns a human-readable name for TUI/logs (e.g. 'Skroutz', 'Amazon')."""
        ...

    @staticmethod
    @abstractmethod
    def get_supported_domains() -> list[str]:
        """Returns the canonical list of domains this scraper handles.

        This is the SINGLE SOURCE OF TRUTH for domain matching. Both the
        client and the storage must reference this list to avoid mismatch.
        """
        ...

    @staticmethod
    @abstractmethod
    def get_config_filename() -> str:
        """Returns the safe JSON config basename (e.g. ``'skroutz.json'``).

        Discovery accepts only letters, digits, dots, underscores and hyphens,
        with a ``.json`` suffix. Paths, whitespace and control characters are
        rejected because this value crosses filesystem and shell-script boundaries.
        """
        ...

    @staticmethod
    @abstractmethod
    def get_client_class() -> type[BaseScraperClient]:
        """Returns the client class for this scraper."""
        ...

    @staticmethod
    @abstractmethod
    def get_storage_class() -> type[BaseDataManager]:
        """Returns the data manager class for this scraper."""
        ...

    def get_requirements_path(self) -> str | None:
        """Absolute path to this plugin's own ``requirements.txt``, or None.

        Resolved next to the plugin's descriptor module, so a new plugin gets
        optional-dependency support for free just by dropping a ``requirements.txt``
        beside its ``plugin.py`` — ``install.sh`` installs it only when the plugin
        is provisioned, and no installer or registry code changes. A plugin that
        needs nothing beyond the core framework simply ships no such file.

        Returns:
            str | None: The absolute path if the file exists, otherwise None.
        """
        req = Path(inspect.getfile(type(self))).with_name("requirements.txt")
        return str(req) if req.is_file() else None

    def get_timer_directives(self) -> dict[str, str]:
        """systemd ``[Timer]`` *trigger* directives for this plugin's generated unit.

        ``install.sh`` builds each plugin's ``<plugin>-scraper.timer`` from this
        mapping, so a plugin can declare its own cadence (e.g. a heavy browser
        scraper that should run less often than the default) without editing any
        shell script. The framework owns *how* a plugin runs (the ``[Service]``
        ExecStart dispatches through ``run.sh --quiet --<plugin>``); the plugin
        owns *when* it runs. Override to change the schedule.

        Only the schedule/trigger is configurable here. ``RandomizedDelaySec`` and
        ``Persistent`` are framework-managed (hardcoded by ``install.sh`` for every
        plugin) and are deliberately *not* settable per plugin — any such keys
        returned here are dropped when the timer is generated.

        Canonical cadence required: the ``OnCalendar`` must be one of the supported
        cadences (the ``OnCalendar`` values in
        ``core.scrapers.base.settings.SUPPORTED_INTERVALS``, e.g. ``"hourly"``, ``"daily"``,
        ``"*-*-* 00/2:00:00"``). Discovery rejects a non-canonical value
        (``registry._validate_plugin_contract``) so the settings panel can always render
        the effective cadence as a friendly key and a user's ``execution_interval``
        override stays within one vocabulary.

        All keys must be systemd-style alphanumeric directive names and all values
        strings without tabs or newlines. Discovery enforces this because the
        descriptor is transported to the POSIX management scripts as tab-separated
        records before being rendered into a unit file.

        Returns:
            dict[str, str]: ``[Timer]`` trigger ``key -> value`` directives. Must
                contain an ``OnCalendar`` set to one of the canonical cadences.
        """
        return {
            "OnCalendar": "hourly",
        }

    def get_setting_specs(self) -> list[SettingSpec]:
        """The ordered :class:`SettingSpec` list describing this plugin's settings.

        Each spec fully declares one ``settings`` field - its JSON key, normalizer,
        default, display and warning (see :mod:`core.scrapers.base.settings`). The registry
        and the settings panel iterate exactly this list, so a scraper adds a
        store-specific setting by returning ``BASE_SETTING_SPECS + [its specs]`` here -
        the single extension point for per-scraper settings, with no change to base
        ``registry``/``status`` code and no parallel settings class to subclass. The
        plugin reads a custom setting's effective value at scrape time through the
        ``self.settings`` accessor injected into its client and storage.

        Kept import-light like the rest of the descriptor: specs are pure stdlib
        dataclasses, so this never pulls in a transport stack.

        Returns:
            list[SettingSpec]: The settings this plugin exposes, in display order.
        """
        return BASE_SETTING_SPECS

    def matches_url(self, url: str) -> bool:
        """Returns True if the URL's host is one this plugin handles.

        The single place the supported-domain match is performed: both the
        registry (URL routing) and a plugin's data manager (storage validation)
        delegate here, so domain matching can never drift between them. Matching is
        label-boundary-aware against ``get_supported_domains()`` (a supported domain
        or a subdomain of it) and tolerant of non-string or empty input. The boundary
        check prevents a host like ``myskroutz.gr`` from falsely matching ``skroutz.gr``.
        Each declared domain is folded (``strip().lower()``) before comparison — the
        same normalization the registry's domain-conflict check applies — so a plugin
        declaring ``"Store.example "`` still routes instead of silently never matching.

        Args:
            url (str): The URL to test.

        Returns:
            bool: True if the URL is on a supported domain.
        """
        if not isinstance(url, str) or not url:
            return False
        try:
            domain = urlparse(url).netloc.lower()
        except ValueError:
            return False
        return any(domain == d or domain.endswith("." + d)
                   for d in (raw.strip().lower() for raw in self.get_supported_domains()))
