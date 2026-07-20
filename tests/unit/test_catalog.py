import json
import sys
import types
from types import MappingProxyType
from unittest import mock

import pytest

from core.exceptions import PluginDependencyError, PluginValidationError
from core.scrapers.api import (
    ItemField,
    ScraperClient,
    ScraperPlugin,
    SettingSpec,
    UrlField,
)
from core.scrapers.check import check_plugin
from core.scrapers.cli import main as cli_main
from core.scrapers.cli import resolve_schedule
from core.scrapers.registry import ClientLoader, PluginCatalog, compile_plugin
from core.settings import SettingStatus, resolve_settings


def _plugin(**changes):
    domains = changes.pop("domains", ("example.test",))
    accepts_url = changes.pop("accepts_url", lambda _url: True)
    custom_fields = changes.pop("item_fields", ())
    url = UrlField("url", domains=domains, accepts_url=accepts_url)
    item_fields = (
        (url, *tuple(custom_fields)) if isinstance(custom_fields, (list, tuple)) else custom_fields
    )
    values = dict(
        display_name="Test",
        item_fields=item_fields,
        settings=(),
        default_interval="1h",
        reference_url=url,
    )
    values.update(changes)
    return ScraperPlugin(**values)


def _compile(definition=None, target="teststore"):
    return compile_plugin(definition or _plugin(), target=target, package=f"tests.plugins.{target}")


@pytest.mark.parametrize("target", ["Bad", "1bad", "general"])
def test_invalid_or_reserved_target_rejected(target):
    with pytest.raises(PluginValidationError):
        _compile(target=target)


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": " "},
        {"domains": ()},
        {"domains": "example.test"},
        {"domains": ("https://x.test",)},
        {"default_interval": "3h"},
        {"accepts_url": lambda _url: "yes"},
        {"item_fields": 1},
        {"settings": 1},
    ],
)
def test_malformed_descriptor_values_are_contextual(changes):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(**changes))


def test_fields_settings_and_canonical_defaults_are_compiled_without_mutation():
    field = ItemField("sku", lambda raw: str(raw).strip(), default="x")
    setting = SettingSpec("region", lambda raw: str(raw).strip(), default="global")
    record = _compile(_plugin(item_fields=[field], settings=[setting]))
    assert record.item_fields[-1] is field
    assert field.default == "x"
    assert setting.default == "global"
    assert setting.display_label == "Region"
    assert "region" in setting.invalid_warning
    assert isinstance(record.settings_by_key, MappingProxyType)
    assert record.setting("region") is setting
    with pytest.raises(TypeError):
        record.settings_by_key["new"] = setting
    with pytest.raises(PluginValidationError, match="not canonical"):
        _compile(_plugin(item_fields=[ItemField("sku", str.strip, default=" x ")]))
    with pytest.raises(PluginValidationError):
        _compile(_plugin(item_fields=[field, field]))
    with pytest.raises(PluginValidationError):
        _compile(_plugin(settings=[setting, setting]))


@pytest.mark.parametrize(
    "field",
    [
        object(),
        ItemField("id", str, default="x"),
        ItemField("", str, default="x"),
        ItemField("sku", None, default="x"),
        ItemField(
            "sku",
            lambda _raw: (_ for _ in ()).throw(ValueError("bad")),
            default="x",
        ),
    ],
)
def test_malformed_item_field_is_rejected(field):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(item_fields=[field]))


@pytest.mark.parametrize(
    "setting",
    [
        object(),
        SettingSpec("", int, default=1),
        SettingSpec("limit", None, default=1),
        SettingSpec(
            "limit",
            lambda _raw: (_ for _ in ()).throw(ValueError("bad")),
            default=1,
        ),
        SettingSpec("limit", int, default=1, display=lambda _value: 2),
        SettingSpec(
            "limit",
            int,
            default=1,
            display=lambda _value: (_ for _ in ()).throw(ValueError("bad")),
        ),
        SettingSpec("limit", int, default=1, label=5),
        SettingSpec("limit", int, default=1, warning=5),
    ],
)
def test_malformed_setting_is_rejected(setting):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(settings=[setting]))


def test_overlapping_domains_are_allowed_between_adapters():
    first = _compile(_plugin(domains=["example.test"]), target="one")
    second = _compile(_plugin(domains=["sub.example.test"]), target="two")
    catalog = PluginCatalog([first, second])
    assert catalog.targets == ("one", "two")
    assert first.canonicalize_url("https://example.test/product") == "https://example.test/product"
    with pytest.raises(ValueError):
        first.canonicalize_url("relative")
    with pytest.raises(KeyError):
        first.setting("missing")
    with pytest.raises(ValueError):
        catalog.get("missing")
    with pytest.raises(PluginValidationError, match="Duplicate"):
        PluginCatalog([first, first])


def test_url_free_and_multiple_url_plugins_compile():
    sku = ItemField("sku", str)
    url_free = _compile(ScraperPlugin(display_name="Identifiers", item_fields=(sku,)))
    assert url_free.domains == ()
    assert url_free.url_fields == ()
    assert url_free.reference_url is None

    product = UrlField("product_url", domains=("products.test",), accepts_url=lambda _url: True)
    seller = UrlField("seller_url", domains=("sellers.test",), accepts_url=lambda _url: True)
    multiple = _compile(
        ScraperPlugin(
            display_name="Multiple",
            item_fields=(product, seller),
            reference_url=seller,
        )
    )
    assert multiple.url_fields == (product, seller)
    assert (
        multiple.canonicalize_url(product, "https://products.test/p#fragment")
        == "https://products.test/p"
    )
    with pytest.raises(ValueError, match="not registered"):
        multiple.canonicalize_url(product, "https://sellers.test/p")


def test_source_package_requires_production_files(tmp_path):
    with pytest.raises(PluginValidationError, match="missing required file"):
        compile_plugin(
            _plugin(),
            target="teststore",
            package="teststore",
            source_dir=tmp_path,
        )


def test_runtime_compilation_does_not_require_contributor_docs(tmp_path):
    for name in ("__init__.py", "plugin.py", "client.py"):
        (tmp_path / name).write_text("", encoding="utf-8")
    record = compile_plugin(
        _plugin(),
        target="teststore",
        package="teststore",
        source_dir=tmp_path,
    )
    assert record.example_config_path.endswith("config.example.json")


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": "Bad\tName"},
        {"item_fields": [ItemField("not-kebab", str, default="x")]},
        {"item_fields": [ItemField("CamelCase", str, default="x")]},
        {"settings": [SettingSpec("bad\nkey", str, default="x")]},
        {"settings": [SettingSpec("safe", str, default="x", label="Bad\x7fLabel")]},
        {"settings": [SettingSpec("safe", str, default="x", display=lambda _value: "Bad\tValue")]},
    ],
)
def test_catalog_strings_and_keys_are_shell_and_terminal_safe(changes):
    with pytest.raises(PluginValidationError):
        _compile(_plugin(**changes))


def test_schedule_missing_and_valid_config(tmp_path):
    plugin = PluginCatalog.discover().get("skroutz")
    missing = resolve_schedule(plugin, str(tmp_path))
    assert missing.status is SettingStatus.NO_CONFIG
    assert missing.on_calendar == "hourly"
    (tmp_path / "skroutz.json").write_text(
        json.dumps(
            {
                "settings": {"execution_interval": "2 hours"},
                "items": [],
            }
        )
    )
    valid = resolve_schedule(plugin, str(tmp_path))
    assert valid.status is SettingStatus.OK
    assert valid.on_calendar == "*-*-* 00/2:00:00"


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
    with mock.patch("core.scrapers.registry.importlib.import_module", side_effect=missing):
        with pytest.raises(PluginDependencyError, match="install.sh --teststore"):
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_plugin_internal_import_failure_is_validation_not_dependency():
    plugin = _compile()
    missing = ModuleNotFoundError(
        "No module named 'tests.plugins.teststore.helper'",
        name="tests.plugins.teststore.helper",
    )
    with mock.patch("core.scrapers.registry.importlib.import_module", side_effect=missing):
        with pytest.raises(PluginValidationError, match="client import failed"):
            ClientLoader().load(plugin, resolve_settings(plugin.setting_specs, {}))


def test_non_import_client_module_defect_is_validation_failure():
    plugin = _compile()
    with mock.patch(
        "core.scrapers.registry.importlib.import_module",
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


def test_verifier_and_manifest_cli(capsys, tmp_path):
    assert "state round-trip" in check_plugin("skroutz")
    assert cli_main(["manifest", "--config-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "skroutz\tSkroutz\t" in output and "\thourly\tnocfg" in output
    assert cli_main(["intervals"]) == 0
    assert "1h" in capsys.readouterr().out
    assert cli_main(["requirements"]) == 0
    requirements_output = capsys.readouterr().out
    assert "skroutz\t" in requirements_output
    with mock.patch("core.scrapers.cli.check_plugin", return_value=["contributor files"]):
        assert cli_main(["plugin-check", "skroutz"]) == 0
    with mock.patch("core.scrapers.cli.PluginCatalog.discover", side_effect=RuntimeError("bad")):
        assert cli_main(["diagnose"]) == 1
