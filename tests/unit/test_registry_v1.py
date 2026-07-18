import json
from urllib.parse import SplitResult
from unittest import mock

import pytest

from core.exceptions import PluginDependencyError, PluginValidationError
from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec
from core.scrapers.check import check_plugin
from core.scrapers.cli import main as cli_main
from core.scrapers.registry import ScraperRegistry
from core.settings import SettingStatus


def _plugin(**changes):
    values = dict(
        display_name="Test", domains=("example.test",), client=".client:Client",
        accepts_url=lambda _url: True, item_fields=(), settings=(), default_interval="1h",
    )
    values.update(changes)
    return ScraperPlugin(**values)


@pytest.fixture
def empty_registry():
    saved, discovered = dict(ScraperRegistry._plugins), ScraperRegistry._discovered
    ScraperRegistry._reset()
    yield
    ScraperRegistry._plugins, ScraperRegistry._discovered = saved, discovered


@pytest.mark.parametrize("target", ["Bad", "1bad", "general"])
def test_invalid_or_reserved_target_rejected(empty_registry, target):
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(), target=target)


@pytest.mark.parametrize("changes", [
    {"display_name": " "}, {"domains": ()}, {"domains": ("https://x.test",)},
    {"client": "client:Thing"}, {"default_interval": "3h"},
    {"accepts_url": lambda _url: "yes"},
])
def test_invalid_descriptor_metadata_rejected(empty_registry, changes):
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(**changes), target="teststore")


def test_duplicate_domains_fields_settings_and_conflicts_rejected(empty_registry):
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(domains=("x.test", "x.test")), target="one")
    field = ItemField("sku", str, "x")
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(item_fields=(field, field)), target="one")
    setting = SettingSpec("region", "Region", str, str, "bad", "global")
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(settings=(setting, setting)), target="one")
    ScraperRegistry.register(_plugin(domains=("example.test",)), target="one")
    with pytest.raises(PluginValidationError):
        ScraperRegistry.register(_plugin(domains=("sub.example.test",)), target="two")


def test_routing_rejects_credentials_and_keeps_query():
    assert ScraperRegistry.plugin_for_url(
        "https://www.skroutz.gr/s/1/Product.html?q=blue#x"
    ).target == "skroutz"
    assert ScraperRegistry.plugin_for_url("https://user:pass@skroutz.gr/s/1/x") is None
    assert ScraperRegistry.plugin_for_url("https://skroutz.gr/search?q=x") is None


def test_schedule_missing_and_valid_config(tmp_path):
    missing = ScraperRegistry.resolve_schedule("skroutz", str(tmp_path))
    assert missing.status is SettingStatus.NO_CONFIG
    assert missing.on_calendar == "hourly"
    (tmp_path / "skroutz.json").write_text(json.dumps({
        "settings": {"execution_interval": "2 hours"}, "items": [],
    }))
    valid = ScraperRegistry.resolve_schedule("skroutz", str(tmp_path))
    assert valid.status is SettingStatus.OK
    assert valid.on_calendar == "*-*-* 00/2:00:00"


def test_missing_lazy_client_symbol_has_actionable_error(empty_registry, tmp_path):
    ScraperRegistry.register(_plugin(client=".missing:Client"), target="teststore")
    registry = ScraperRegistry(str(tmp_path))
    plugin = ScraperRegistry.get_plugin("teststore")
    from core.settings import resolve_settings
    registry.prime_settings("teststore", resolve_settings(plugin.setting_specs, {}))
    with pytest.raises(PluginDependencyError) as caught:
        registry.get_client("teststore")
    assert "install.sh --teststore" in str(caught.value)


def test_direct_verifier_and_cli_views(capsys, tmp_path):
    with mock.patch("core.scrapers.check.HEAVY_IMPORT_ROOTS", frozenset()):
        assert "state round-trip" in check_plugin("skroutz")
    assert cli_main(["plugins", "--view", "targets"]) == 0
    assert "skroutz" in capsys.readouterr().out
    assert cli_main(["intervals"]) == 0
    assert "1h" in capsys.readouterr().out
    assert cli_main(["schedules", "--view", "status", "--config-dir", str(tmp_path)]) == 0
    assert "nocfg" in capsys.readouterr().out
    with mock.patch("core.scrapers.cli.check_plugin", return_value=["metadata"]):
        assert cli_main(["plugin-check", "skroutz"]) == 0
    with mock.patch("core.scrapers.cli.ScraperRegistry.registered_targets", side_effect=RuntimeError("bad")):
        assert cli_main(["diagnose"]) == 1
