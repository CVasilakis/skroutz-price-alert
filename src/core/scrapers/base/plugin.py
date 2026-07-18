"""Import-light declarative contracts for scraper plugin discovery."""

from dataclasses import dataclass

from core.scrapers.base.settings import SettingSpec
from core.scrapers.base.url import url_matches_domains


@dataclass(frozen=True)
class ClassRef:
    """A lazily imported class binding declared by module and symbol name.

    Relative modules (for example ``.client``) resolve against the plugin package.
    Keeping bindings as data lets discovery enumerate plugins without importing their
    transport or parsing dependencies.
    """

    module: str
    symbol: str


@dataclass(frozen=True)
class PluginDefinition:
    """Contributor-authored metadata for one scraper package.

    The package directory supplies the machine target name and config filename. Only
    store-specific settings belong in ``setting_specs``; the registry adds the shared
    scraper settings automatically.
    """

    display_name: str
    domains: tuple[str, ...]
    client: ClassRef
    storage: ClassRef
    default_interval: str = "1h"
    setting_specs: tuple[SettingSpec, ...] = ()


@dataclass(frozen=True)
class RegisteredPlugin:
    """Validated, normalized runtime record produced by the registry."""

    target: str
    display_name: str
    domains: tuple[str, ...]
    client: ClassRef
    storage: ClassRef
    default_interval: str
    setting_specs: tuple[SettingSpec, ...]
    package: str
    source_dir: str
    example_config_path: str
    requirements_path: str | None

    @property
    def config_filename(self) -> str:
        """Return the framework-derived user config filename."""
        return f"{self.target}.json"

    def matches_url(self, url: str) -> bool:
        """Return whether ``url`` belongs to one of this plugin's domains."""
        return url_matches_domains(url, self.domains)
