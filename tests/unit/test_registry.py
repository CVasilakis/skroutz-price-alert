"""Unit tests for declarative plugin discovery and validation."""

import contextlib
import types
import unittest
from pathlib import Path
from unittest import mock

from core.exceptions import PluginDiscoveryError, PluginValidationError
from core.scrapers.base.plugin import ClassRef, PluginDefinition
from core.scrapers.base.settings import SettingSpec
from core.scrapers.registry import RESERVED_PLUGIN_NAMES, ScraperRegistry
from support import fake_plugin, registry_sandbox


def _register(fixture, *, source_dir=None):
    ScraperRegistry.register(
        fixture.definition,
        target=fixture.target,
        source_dir=source_dir,
    )


def _spec(key: str) -> SettingSpec:
    return SettingSpec(key, "Label", lambda raw: raw, str, "warning")


class TestRegisterValidationGate(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(registry_sandbox())

    def test_valid_definition_registers_and_routes_normalized_domain(self):
        _register(fake_plugin(domains=("Store.Example.",)))
        plugin = ScraperRegistry.plugin_for_url("https://shop.store.example/item/1")
        self.assertIsNotNone(plugin)
        assert plugin is not None
        self.assertEqual(plugin.target, "fakestore")
        self.assertEqual(plugin.config_filename, "fakestore.json")
        self.assertEqual(plugin.domains, ("store.example",))

    def test_non_definition_is_rejected(self):
        with self.assertRaises(PluginValidationError):
            ScraperRegistry.register(object(), target="bad")  # type: ignore[arg-type]

    def test_duplicate_target_is_rejected(self):
        _register(fake_plugin())
        with self.assertRaisesRegex(PluginValidationError, "Duplicate plugin target"):
            _register(fake_plugin(domains=("elsewhere.example",)))

    def test_target_must_be_safe_and_not_reserved(self):
        for target in ("bad-name", "9bad", "two words", "UpperCase"):
            with self.subTest(target=target), self.assertRaises(PluginValidationError):
                _register(fake_plugin(name=target))
        for target in sorted(RESERVED_PLUGIN_NAMES):
            with self.subTest(target=target), self.assertRaises(PluginValidationError):
                _register(fake_plugin(name=target))

    def test_display_name_domains_and_interval_are_validated(self):
        invalid = (
            fake_plugin(display_name=" "),
            fake_plugin(domains=()),
            fake_plugin(domains=("https://bad.example/path",)),
            fake_plugin(default_interval="3h"),
        )
        for fixture in invalid:
            with self.subTest(fixture=fixture), self.assertRaises(PluginValidationError):
                _register(fixture)

    def test_duplicate_and_overlapping_domains_are_rejected(self):
        with self.assertRaisesRegex(PluginValidationError, "duplicate normalized domain"):
            _register(fake_plugin(domains=("EXAMPLE.com", "example.com.")))
        _register(fake_plugin(name="first", domains=("example.com",)))
        with self.assertRaisesRegex(PluginValidationError, "Domain conflict"):
            _register(fake_plugin(name="second", domains=("shop.example.com",)))

    def test_class_references_are_structurally_validated_without_importing(self):
        for ref in (ClassRef("bad-module!", "Client"), ClassRef(".client", "bad-name")):
            definition = PluginDefinition(
                "Bad", ("bad.example",), ref, ClassRef(".storage", "Storage")
            )
            with self.subTest(ref=ref), self.assertRaises(PluginValidationError):
                ScraperRegistry.register(definition, target="bad")

    def test_framework_settings_are_added_and_custom_keys_cannot_collide(self):
        _register(fake_plugin(specs=(_spec("region"),)))
        keys = [spec.key for spec in ScraperRegistry.get_plugin("fakestore").setting_specs]
        self.assertEqual(keys[-1], "region")
        self.assertIn("execution_interval", keys)

        ScraperRegistry._reset()
        with self.assertRaisesRegex(PluginValidationError, "duplicate setting key"):
            _register(fake_plugin(specs=(_spec("execution_interval"),)))

    def test_registered_metadata_is_immutable_and_defensively_normalized(self):
        _register(fake_plugin(domains=("STORE.EXAMPLE.",)))
        plugin = ScraperRegistry.get_plugin("fakestore")
        self.assertEqual(plugin.domains, ("store.example",))
        with self.assertRaises((AttributeError, TypeError)):
            plugin.domains += ("mutated.example",)  # type: ignore[misc]

    def test_source_paths_are_derived_from_the_package_directory(self):
        source = Path(__file__).resolve().parents[2] / "src/core/scrapers/skroutz"
        _register(fake_plugin(name="skroutz", domains=("skroutz.example",)), source_dir=source)
        plugin = ScraperRegistry.get_plugin("skroutz")
        self.assertEqual(plugin.example_config_path, str(source / "config.example.json"))
        self.assertEqual(plugin.requirements_path, str(source / "requirements.txt"))


class TestDiscoverPackageShape(unittest.TestCase):
    def _discover(self, module=None, import_error=None):
        importer = mock.Mock(side_effect=import_error) if import_error else mock.Mock(return_value=module)
        with registry_sandbox(frozen=False), mock.patch(
            "core.scrapers.registry.pkgutil.iter_modules", return_value=[(None, "fakepkg", True)]
        ), mock.patch("core.scrapers.registry.importlib.import_module", importer):
            ScraperRegistry.discover()
            return dict(ScraperRegistry._plugins)

    def test_unimportable_descriptor_fails_loudly(self):
        with self.assertRaises(PluginDiscoveryError):
            self._discover(import_error=ImportError("missing"))

    def test_missing_or_wrong_plugin_constant_fails_loudly(self):
        with self.assertRaises(PluginDiscoveryError):
            self._discover(types.SimpleNamespace())
        with self.assertRaises(PluginValidationError):
            self._discover(types.SimpleNamespace(PLUGIN=object()))

    def test_well_formed_descriptor_registers(self):
        fixture = fake_plugin(name="fakepkg")
        registered = self._discover(types.SimpleNamespace(PLUGIN=fixture.definition))
        self.assertIn("fakepkg", registered)

    def test_failed_discovery_rolls_back_partial_registration(self):
        good = fake_plugin(name="first", domains=("first.example",))
        modules = {
            "core.scrapers.first.plugin": types.SimpleNamespace(PLUGIN=good.definition),
            "core.scrapers.second.plugin": types.SimpleNamespace(),
        }
        with registry_sandbox(frozen=False), mock.patch(
            "core.scrapers.registry.pkgutil.iter_modules",
            return_value=[(None, "first", True), (None, "second", True)],
        ), mock.patch(
            "core.scrapers.registry.importlib.import_module",
            side_effect=lambda name: modules[name],
        ):
            with self.assertRaises(PluginDiscoveryError):
                ScraperRegistry.discover()
            self.assertEqual(ScraperRegistry._plugins, {})


if __name__ == "__main__":
    unittest.main()
