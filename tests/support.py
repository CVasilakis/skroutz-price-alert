"""Shared test helpers for the current plugin architecture."""

import contextlib
import json
import sys
import types
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

from core.application.contracts import RunReporter
from core.general import general_config_path
from core.notifications.contracts import NotificationService
from core.scrapers.api import ScraperClient, ScraperPlugin, TrackedItem, UrlField
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.compiler import compile_plugin
from core.scrapers.framework.configuration import SCHEMA_VERSION, decode_target_document
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import ResolvedSettings


@dataclass(frozen=True)
class PluginFixture:
    target: str
    definition: ScraperPlugin
    client_class: type[ScraperClient] | None = None


@dataclass(frozen=True)
class PluginTestConfig:
    """Framework-decoded values for plugin-owned behavior tests."""

    settings: ResolvedSettings
    items: tuple[TrackedItem, ...]


def fake_plugin(
    name="fakestore",
    domains=("fake-store.example",),
    display_name="Fake Store",
    specs=None,
    fields=None,
    client_class=None,
    default_interval="1h",
    accepts_url=None,
    url_field=None,
) -> PluginFixture:
    url = url_field or UrlField(
        key="url",
        domains=tuple(domains),
        accepts_url=accepts_url or (lambda _url: True),
    )
    definition = ScraperPlugin(
        display_name=display_name,
        item_fields=(url, *tuple(fields or ())),
        settings=tuple(specs or ()),
        default_interval=default_interval,
        reference_url=url,
    )
    return PluginFixture(name, definition, client_class)


def compile_test_plugin(
    definition: ScraperPlugin,
    target: str,
    *,
    source_dir: str | Path | None = None,
) -> RegisteredPlugin:
    """Compile one descriptor without discovering or importing sibling plugins."""
    return compile_plugin(
        definition,
        target=target,
        package=f"core.scrapers.plugins.{target}",
        source_dir=source_dir,
    )


def decode_test_config(
    definition: ScraperPlugin,
    target: str,
    *,
    settings: Mapping[str, object] | None = None,
    items: Sequence[Mapping[str, object]] = (),
) -> PluginTestConfig:
    """Decode raw plugin test values through the production configuration contract."""
    plugin = compile_test_plugin(definition, target)
    decoded = decode_target_document(
        plugin,
        {
            "schema_version": SCHEMA_VERSION,
            "plugin_schema_version": definition.config_schema_version,
            "settings": dict(settings) if settings is not None else {},
            "items": [dict(item) for item in items],
        },
    )
    if decoded.row_issues:
        detail = "; ".join(f"item {issue.index}: {issue.message}" for issue in decoded.row_issues)
        raise ValueError(f"invalid plugin test configuration: {detail}")
    return PluginTestConfig(decoded.settings, decoded.items)


def synthetic_catalog(*plugins: PluginFixture | RegisteredPlugin) -> PluginCatalog:
    """Build a catalog from explicit synthetic or already-compiled plugins."""
    return PluginCatalog(
        compile_test_plugin(plugin.definition, plugin.target)
        if isinstance(plugin, PluginFixture)
        else plugin
        for plugin in plugins
    )


@contextlib.contextmanager
def catalog_sandbox(*plugins: PluginFixture | RegisteredPlugin):
    added_modules: list[str] = []
    try:
        records = []
        for plugin in plugins:
            if isinstance(plugin, PluginFixture):
                package = f"core.scrapers.plugins.{plugin.target}"
                if plugin.client_class is not None:
                    module_name = f"{package}.client"
                    module = types.ModuleType(module_name)
                    module.Client = plugin.client_class
                    sys.modules[module_name] = module
                    added_modules.append(module_name)
                records.append(
                    compile_plugin(
                        plugin.definition,
                        target=plugin.target,
                        package=package,
                    )
                )
            else:
                records.append(plugin)
        yield PluginCatalog(records)
    finally:
        for name in added_modules:
            sys.modules.pop(name, None)


def mock_notifier(has_services: bool = False, delivery_ok: bool = True) -> mock.Mock:
    notifier = mock.create_autospec(NotificationService, instance=True)
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
