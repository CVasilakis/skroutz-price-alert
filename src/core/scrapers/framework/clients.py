"""Conventional, one-shot scraper client loading."""

from __future__ import annotations

import importlib

from core import messages
from core.exceptions import PluginDependencyError, PluginValidationError
from core.scrapers.api import ScraperClient
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import ResolvedSettings


class ClientLoader:
    """Load one conventional client without retaining lifecycle state."""

    @staticmethod
    def _import_failure(plugin: RegisteredPlugin, exc: ImportError) -> Exception:
        missing = getattr(exc, "name", None)
        internal = (
            not missing
            or missing == plugin.package
            or plugin.package.startswith(f"{missing}.")
            or missing.startswith(f"{plugin.package}.")
            or missing == "core"
            or missing.startswith("core.")
        )
        if internal:
            return PluginValidationError(f"Plugin '{plugin.target}' client import failed: {exc}")
        return PluginDependencyError(messages.plugin_dependency_detail(plugin.target, missing))

    def load(self, plugin: RegisteredPlugin, settings: ResolvedSettings) -> ScraperClient:
        """Import one plugin's ``client.py`` and construct its ``Client``.

        Deferred until the target actually runs, which is what allows a plugin to
        have private dependencies without every other command paying to import
        them.

        Import failures are separated by blame, because the two need opposite
        responses from the user: a missing *third-party* module is a dependency
        problem with an actionable install hint, while a failure to import the
        plugin's own modules (or ``core``) is a defect in the plugin.

        Raises:
            PluginDependencyError: A private dependency is not installed.
            PluginValidationError: The plugin is broken — no ``Client``, wrong base
                class, or its own import or construction failed.
        """
        module_name = f"{plugin.package}.client"
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise self._import_failure(plugin, exc) from exc
        except Exception as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' client import failed: {exc}"
            ) from exc
        try:
            client_type = module.Client
        except AttributeError as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' must export Client from client.py."
            ) from exc
        if not isinstance(client_type, type) or not issubclass(client_type, ScraperClient):
            raise PluginValidationError(
                f"Plugin '{plugin.target}' Client must be a ScraperClient subclass."
            )
        try:
            return client_type(settings)
        except ImportError as exc:
            raise self._import_failure(plugin, exc) from exc
        except Exception as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' Client construction failed: {type(exc).__name__}: {exc}"
            ) from exc


__all__ = ["ClientLoader"]
