import json
import sys
import types
from unittest import mock

import pytest

from core.exceptions import PluginDependencyError, PluginValidationError
from core.scrapers.api import ItemField, ScraperClient, ScraperPlugin, SettingSpec
from core.scrapers.check import check_plugin
from core.scrapers.cli import main as cli_main, resolve_schedule
from core.scrapers.registry import ClientFactory, PluginCatalog, compile_plugin, setting_spec
from core.settings import SettingStatus, resolve_settings


def _plugin(**changes):
    values = dict(
        display_name="Test",
        domains=("example.test",),
        accepts_url=lambda _url: True,
        item_fields=(),
        settings=(),
        default_interval="1h",
    )
    values.update(changes)
    return ScraperPlugin(**values)


def _compile(definition=None, target="teststore"):
    return compile_plugin(
        definition or _plugin(), target=target, package=f"tests.plugins.{target}"
    )


@pytest.mark.parametrize("target", ["Bad", "1bad", "general"])
def test_invalid_or_reserved_target_rejected(target):
    with pytest.raises(PluginValidationError):
        _compile(target=target)


@pytest.mark.parametrize("changes", [
    {"display_name": " "},
    {"domains": ()},
    {"domains": "example.test"},
    {"domains": ("https://x.test",)},
    {"default_interval": "3h"},
    {"accepts_url": lambda _url: "yes"},
    {"item_fields": 1},
    {"settings": 1},
])
def test_malformed_descriptor_values_are_contextual(changes):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(**changes))


def test_fields_settings_and_defaults_are_compiled_once():
    field = ItemField("sku", lambda raw: str(raw).strip(), " x ")
    setting = SettingSpec("region", " global ", lambda raw: str(raw).strip())
    record = _compile(_plugin(item_fields=[field], settings=[setting]))
    assert record.item_fields == (field,)
    assert field.default == "x"
    assert setting.default == "global"
    assert setting.display_label == "Region"
    assert "region" in setting.invalid_warning
    with pytest.raises(PluginValidationError):
        _compile(_plugin(item_fields=[field, field]))
    with pytest.raises(PluginValidationError):
        _compile(_plugin(settings=[setting, setting]))


@pytest.mark.parametrize("field", [
    object(),
    ItemField("id", str, "x"),
    ItemField("", str, "x"),
    ItemField("sku", None, "x"),
    ItemField("sku", lambda _raw: (_ for _ in ()).throw(ValueError("bad")), "x"),
])
def test_malformed_item_field_is_rejected(field):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(item_fields=[field]))


@pytest.mark.parametrize("setting", [
    object(),
    SettingSpec("", 1, int),
    SettingSpec("limit", 1, None),
    SettingSpec("limit", 1, lambda _raw: (_ for _ in ()).throw(ValueError("bad"))),
    SettingSpec("limit", 1, int, display=lambda _value: 2),
    SettingSpec("limit", 1, int, display=lambda _value: (_ for _ in ()).throw(ValueError("bad"))),
    SettingSpec("limit", 1, int, label=5),
    SettingSpec("limit", 1, int, warning=5),
])
def test_malformed_setting_is_rejected(setting):
    with pytest.raises(PluginValidationError, match="Plugin 'teststore'"):
        _compile(_plugin(settings=[setting]))


def test_overlapping_domains_are_allowed_between_adapters():
    first = _compile(_plugin(domains=["example.test"]), target="one")
    second = _compile(_plugin(domains=["sub.example.test"]), target="two")
    catalog = PluginCatalog([first, second])
    assert catalog.targets == ("one", "two")
    assert first.accepts("https://example.test/product")
    assert not first.accepts("relative")
    with pytest.raises(KeyError):
        setting_spec(first, "missing")
    with pytest.raises(ValueError):
        catalog.get("missing")
    with pytest.raises(PluginValidationError, match="Duplicate"):
        PluginCatalog([first, first])


def test_source_package_requires_production_files(tmp_path):
    with pytest.raises(PluginValidationError, match="missing required file"):
        compile_plugin(
            _plugin(), target="teststore", package="teststore", source_dir=tmp_path,
        )


def test_schedule_missing_and_valid_config(tmp_path):
    plugin = PluginCatalog.discover().get("skroutz")
    missing = resolve_schedule(plugin, str(tmp_path))
    assert missing.status is SettingStatus.NO_CONFIG
    assert missing.on_calendar == "hourly"
    (tmp_path / "skroutz.json").write_text(json.dumps({
        "settings": {"execution_interval": "2 hours"}, "items": [],
    }))
    valid = resolve_schedule(plugin, str(tmp_path))
    assert valid.status is SettingStatus.OK
    assert valid.on_calendar == "*-*-* 00/2:00:00"


def test_conventional_client_is_lazy_cached_and_closed():
    class Client(ScraperClient):
        def scrape(self, item):
            raise NotImplementedError

    plugin = _compile()
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    module.Client = Client
    sys.modules[module_name] = module
    settings = resolve_settings(plugin.setting_specs, {})
    factory = ClientFactory()
    try:
        assert factory.create(plugin, settings) is factory.create(plugin, settings)
    finally:
        factory.close()
        sys.modules.pop(module_name, None)


def test_missing_client_module_has_dependency_guidance():
    plugin = _compile()
    factory = ClientFactory()
    with pytest.raises(PluginDependencyError, match="install.sh --teststore"):
        factory.create(plugin, resolve_settings(plugin.setting_specs, {}))


def test_conventional_client_symbol_and_type_are_validated():
    plugin = _compile()
    settings = resolve_settings(plugin.setting_specs, {})
    module_name = f"{plugin.package}.client"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    try:
        with pytest.raises(PluginValidationError, match="export Client"):
            ClientFactory().create(plugin, settings)
        module.Client = object
        with pytest.raises(PluginValidationError, match="ScraperClient subclass"):
            ClientFactory().create(plugin, settings)
    finally:
        sys.modules.pop(module_name, None)


def test_client_factory_closes_all_even_when_one_close_fails():
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
    factory = ClientFactory()
    factory.create(plugin, resolve_settings(plugin.setting_specs, {}))
    try:
        with pytest.raises(RuntimeError, match="close failed"):
            factory.close()
    finally:
        sys.modules.pop(module_name, None)


def test_verifier_and_manifest_cli(capsys, tmp_path):
    with mock.patch("core.scrapers.check.HEAVY_IMPORT_ROOTS", frozenset()):
        assert "state round-trip" in check_plugin("skroutz")
    assert cli_main(["manifest", "--config-dir", str(tmp_path)]) == 0
    output = capsys.readouterr().out
    assert "skroutz\tSkroutz\t" in output and "\thourly\tnocfg" in output
    assert cli_main(["intervals"]) == 0
    assert "1h" in capsys.readouterr().out
    with mock.patch("core.scrapers.cli.check_plugin", return_value=["metadata"]):
        assert cli_main(["plugin-check", "skroutz"]) == 0
    with mock.patch("core.scrapers.cli.PluginCatalog.discover", side_effect=RuntimeError("bad")):
        assert cli_main(["diagnose"]) == 1
