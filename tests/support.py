"""Shared test helpers for the current plugin architecture."""

import contextlib
import json
import sys
import types
from dataclasses import dataclass
from unittest import mock

from core.general import general_config_path
from core.notifier import Notifier
from core.scrapers.api import ScraperClient, ScraperPlugin
from core.run import RunReporter
from core.scrapers.registry import PluginCatalog, RegisteredPlugin, compile_plugin


@dataclass(frozen=True)
class PluginFixture:
    target: str
    definition: ScraperPlugin
    client_class: type[ScraperClient] | None = None


def fake_plugin(name="fakestore", domains=("fake-store.example",),
                display_name="Fake Store", specs=None, fields=None,
                client_class=None, default_interval="1h", accepts_url=None) -> PluginFixture:
    definition = ScraperPlugin(
        display_name=display_name,
        domains=tuple(domains),
        accepts_url=accepts_url or (lambda _url: True),
        item_fields=tuple(fields or ()), settings=tuple(specs or ()),
        default_interval=default_interval,
    )
    return PluginFixture(name, definition, client_class)


@contextlib.contextmanager
def catalog_sandbox(*plugins: PluginFixture | RegisteredPlugin):
    added_modules: list[str] = []
    try:
        records = []
        for plugin in plugins:
            if isinstance(plugin, PluginFixture):
                package = f"core.scrapers.{plugin.target}"
                if plugin.client_class is not None:
                    module_name = f"{package}.client"
                    module = types.ModuleType(module_name)
                    module.Client = plugin.client_class
                    sys.modules[module_name] = module
                    added_modules.append(module_name)
                records.append(compile_plugin(
                    plugin.definition, target=plugin.target, package=package,
                ))
            else:
                records.append(plugin)
        yield PluginCatalog(records)
    finally:
        for name in added_modules:
            sys.modules.pop(name, None)


def mock_notifier(has_services: bool = False, delivery_ok: bool = True) -> mock.Mock:
    notifier = mock.create_autospec(Notifier, instance=True)
    notifier.has_services = has_services
    notifier.notify_low_price.return_value = delivery_ok
    return notifier


def mock_ui() -> mock.Mock:
    return mock.create_autospec(RunReporter, instance=True)


def write_general(cfg_dir, data) -> None:
    with open(general_config_path(str(cfg_dir)), "w") as file:
        json.dump(data, file)


def read_general(cfg_dir):
    with open(general_config_path(str(cfg_dir))) as file:
        return json.load(file)
