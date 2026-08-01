import sys
import types
from unittest import mock

import pytest

from core.exceptions import PluginDependencyError, PluginValidationError
from core.scrapers.api import ScraperClient, ScraperPlugin, UrlField
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.compiler import compile_plugin
from core.settings import resolve_settings


def _compile():
    url = UrlField("url", domains=("example.test",), accepts_url=lambda _url: True)
    return compile_plugin(
        ScraperPlugin(display_name="Test", item_fields=(url,), reference_url=url),
        target="teststore",
        package="tests.plugins.teststore",
    )


def test_conventional_client_loader_returns_one_shot_instances():
    class Client(ScraperClient):
        def scrape(self, item):
            raise NotImplementedError

    plugin = _compile()
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    module.Client = Client
    sys.modules[module_name] = module
    settings = resolve_settings(plugin.setting_specs, {})
    loader = ClientLoader()
    try:
        first = loader.load(plugin, settings)
        second = loader.load(plugin, settings)
        assert first is not second
    finally:
        first.close()
        second.close()
        sys.modules.pop(module_name, None)


def test_missing_client_module_is_a_plugin_validation_failure():
    plugin = _compile()
    with pytest.raises(PluginValidationError, match="client import failed"):
        ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_third_party_import_failure_gets_dependency_guidance():
    plugin = _compile()
    missing = ModuleNotFoundError("No module named 'optional_transport'", name="optional_transport")
    with mock.patch("core.scrapers.framework.clients.importlib.import_module", side_effect=missing):
        with pytest.raises(PluginDependencyError, match="scrooge-alert install --teststore"):
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_plugin_internal_import_failure_is_validation_not_dependency():
    plugin = _compile()
    missing = ModuleNotFoundError(
        "No module named 'tests.plugins.teststore.helper'",
        name="tests.plugins.teststore.helper",
    )
    with mock.patch("core.scrapers.framework.clients.importlib.import_module", side_effect=missing):
        with pytest.raises(PluginValidationError, match="client import failed"):
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_non_import_client_module_defect_is_validation_failure():
    plugin = _compile()
    with mock.patch(
        "core.scrapers.framework.clients.importlib.import_module",
        side_effect=RuntimeError("module exploded"),
    ):
        with pytest.raises(PluginValidationError, match="client import failed"):
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_conventional_client_symbol_and_type_are_validated():
    plugin = _compile()
    settings = resolve_settings(plugin.setting_specs, {})
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    try:
        with pytest.raises(PluginValidationError, match="export Client"):
            ClientLoader().load(plugin, settings)
        module.Client = object
        with pytest.raises(PluginValidationError, match="ScraperClient subclass"):
            ClientLoader().load(plugin, settings)
    finally:
        sys.modules.pop(module_name, None)


def test_incompatible_client_constructor_is_a_contextual_validation_failure():
    class Client(ScraperClient):
        def __init__(self):
            pass

        def scrape(self, item):
            raise NotImplementedError

    plugin = _compile()
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    module.Client = Client
    sys.modules[module_name] = module
    try:
        with pytest.raises(
            PluginValidationError,
            match=r"Plugin 'teststore' Client construction failed: TypeError:",
        ) as caught:
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))
        assert isinstance(caught.value.__cause__, TypeError)
    finally:
        sys.modules.pop(module_name, None)


def test_client_initialization_failure_is_a_contextual_validation_failure():
    failure = RuntimeError("initialization broke")

    class Client(ScraperClient):
        def __init__(self, settings):
            raise failure

        def scrape(self, item):
            raise NotImplementedError

    plugin = _compile()
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    module.Client = Client
    sys.modules[module_name] = module
    try:
        with pytest.raises(
            PluginValidationError,
            match=(
                r"Plugin 'teststore' Client construction failed: "
                r"RuntimeError: initialization broke"
            ),
        ) as caught:
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))
        assert caught.value.__cause__ is failure
    finally:
        sys.modules.pop(module_name, None)


def test_client_lifecycle_remains_with_the_caller():
    class Client(ScraperClient):
        def scrape(self, item):
            raise NotImplementedError

        def close(self):
            raise RuntimeError("close failed")

    plugin = _compile()
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    module.Client = Client
    sys.modules[module_name] = module
    client = ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))
    try:
        with pytest.raises(RuntimeError, match="close failed"):
            client.close()
    finally:
        sys.modules.pop(module_name, None)
