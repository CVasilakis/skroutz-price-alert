"""Shared test helpers for the current plugin architecture."""

import contextlib
import json
import shutil
import sys
import tempfile
import types
from dataclasses import dataclass
from unittest import mock

from core.general import general_config_path
from core.notifier import Notifier
from core.scrapers.api import ScraperClient, ScraperPlugin
from core.scrapers.registry import RegisteredPlugin, ScraperRegistry
from core.ui.tui import ExecutionStrategy


@dataclass(frozen=True)
class PluginFixture:
    target: str
    definition: ScraperPlugin
    client_class: type[ScraperClient] | None = None


def fake_plugin(name="fakestore", domains=("fake-store.example",),
                display_name="Fake Store", specs=None, fields=None,
                client_class=None, default_interval="1h") -> PluginFixture:
    definition = ScraperPlugin(
        display_name=display_name,
        domains=tuple(domains),
        client=f".client:{client_class.__name__ if client_class else 'MissingFakeClient'}",
        accepts_url=lambda _url: True,
        item_fields=tuple(fields or ()), settings=tuple(specs or ()),
        default_interval=default_interval,
    )
    return PluginFixture(name, definition, client_class)


@contextlib.contextmanager
def registry_sandbox(*plugins: PluginFixture | RegisteredPlugin, frozen: bool = True):
    saved_plugins = dict(ScraperRegistry._plugins)
    saved_discovered = ScraperRegistry._discovered
    added_modules: list[str] = []
    ScraperRegistry._reset()
    try:
        for plugin in plugins:
            if isinstance(plugin, PluginFixture):
                package = f"core.scrapers.{plugin.target}"
                if plugin.client_class is not None:
                    module_name = f"{package}.client"
                    module = types.ModuleType(module_name)
                    setattr(module, plugin.client_class.__name__, plugin.client_class)
                    sys.modules[module_name] = module
                    added_modules.append(module_name)
                ScraperRegistry.register(plugin.definition, target=plugin.target, package=package)
            else:
                ScraperRegistry._plugins[plugin.target] = plugin
        ScraperRegistry._discovered = frozen
        yield ScraperRegistry
    finally:
        for name in added_modules:
            sys.modules.pop(name, None)
        ScraperRegistry._plugins = saved_plugins
        ScraperRegistry._discovered = saved_discovered


def mock_notifier(has_services: bool = False, delivery_ok: bool = True) -> mock.Mock:
    notifier = mock.create_autospec(Notifier, instance=True)
    notifier.has_services = has_services
    notifier.notify_low_price.return_value = delivery_ok
    return notifier


def mock_ui() -> mock.Mock:
    return mock.create_autospec(ExecutionStrategy, instance=True)


def write_general(cfg_dir, data) -> None:
    with open(general_config_path(str(cfg_dir)), "w") as file:
        json.dump(data, file)


def read_general(cfg_dir):
    with open(general_config_path(str(cfg_dir))) as file:
        return json.load(file)
