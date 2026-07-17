from abc import ABC, abstractmethod
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.base.settings import SettingSpec, BASE_SETTING_SPECS
from core.scrapers.base.url import url_matches_domains


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
        ship dependencies in a colocated ``requirements.txt`` (whose path the
        registry computes) and stay uninstalled without breaking discovery.
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
        """Returns host-only DNS names/IPs this scraper handles.

        This is the SINGLE SOURCE OF TRUTH for domain matching. Both the
        client and the storage must reference this list to avoid mismatch.
        """
        ...

    @staticmethod
    @abstractmethod
    def get_config_filename() -> str:
        """Returns the safe JSON config basename (e.g. ``'skroutz.json'``).

        Discovery also reserves ``general.json`` and rejects case-insensitive
        collisions with other plugins.
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
        """Deprecated sentinel; dependency files are discovered by the registry."""
        return None

    def get_timer_directives(self) -> dict[str, str]:
        """Deprecated sentinel; plugins must implement :meth:`get_default_interval`."""
        return {"OnCalendar": "hourly"}

    def get_default_interval(self) -> str:
        """Return the canonical default execution interval key (for example ``1h``)."""
        return "1h"

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
        DNS names are IDNA/case normalized, trailing dots are removed, and explicit
        URL ports are valid but ignored for routing. Only absolute HTTP(S) URLs qualify.

        Args:
            url (str): The URL to test.

        Returns:
            bool: True if the URL is on a supported domain.
        """
        return url_matches_domains(url, self.get_supported_domains())
