"""Unit tests for the registry's registration validation gate.

``ScraperRegistry.register`` is the single gate every plugin passes through —
whether it arrives via ``discover()`` or a direct call — so a malformed
descriptor or an ambiguously-routed domain must be rejected here. These tests
register fake, import-light plugin descriptors (the shared ``support.fake_plugin``
factory) against a clean registry (the shared ``support.registry_sandbox``) and
assert each contract rule fires. No client/storage class is ever resolved, so no
transport library loads.
"""

import contextlib
import os
import types
import unittest
from typing import cast
from unittest import mock

from core.exceptions import PluginDiscoveryError
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.registry import RESERVED_PLUGIN_NAMES, ScraperRegistry
from core.scrapers.base.settings import BASE_SETTING_SPECS

from support import fake_plugin as _fake_plugin, registry_sandbox


class TestRegisterValidationGate(unittest.TestCase):
    def setUp(self):
        # Snapshot the process-wide registry state and start from a clean slate,
        # so fake registrations never leak into (or inherit from) other tests.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(registry_sandbox())

    def test_valid_plugin_registers_and_routes_its_domain(self):
        ScraperRegistry.register(_fake_plugin())
        plugin = ScraperRegistry.plugin_for_url("https://www.fake-store.example/item/1")
        assert plugin is not None, "expected the fake plugin to route its domain"
        self.assertEqual(plugin.get_name(), "fakestore")

    def test_malformed_url_returns_no_plugin(self):
        ScraperRegistry.register(_fake_plugin())
        self.assertIsNone(ScraperRegistry.plugin_for_url("https://["))

    def test_non_plugin_instance_is_rejected(self):
        # cast() forges the wrong type on purpose: the gate must reject it at runtime.
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry.register(cast(BasePlugin, object()))

    def test_duplicate_name_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(domains=("fake-store.example",)))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(domains=("elsewhere.example",)))
        self.assertIn("Duplicate plugin name", str(ctx.exception))

    def test_hyphenated_name_is_rejected(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(name="my-store"))
        self.assertIn("letters, digits and underscores", str(ctx.exception))

    def test_reserved_cli_flag_names_are_rejected(self):
        # Each of these is a built-in '--<flag>' the management scripts match
        # before their per-plugin branch, so a plugin by that name could never
        # be selected from the command line.
        for name in sorted(RESERVED_PLUGIN_NAMES):
            with self.subTest(name=name):
                with self.assertRaises(PluginDiscoveryError) as ctx:
                    ScraperRegistry.register(_fake_plugin(name=name))
                self.assertIn("reserved", str(ctx.exception))
                self.assertNotIn(name, ScraperRegistry._plugins)

    def test_unsafe_config_filenames_are_rejected(self):
        for filename in ("../escape.json", "nested/store.json", "two words.json",
                         "store.txt", "store.json\nother"):
            with self.subTest(filename=filename):
                with self.assertRaises(PluginDiscoveryError) as ctx:
                    ScraperRegistry.register(
                        _fake_plugin(name="badconfig", config=filename)
                    )
                self.assertIn("safe JSON basename", str(ctx.exception))

    def test_custom_safe_config_filename_is_accepted(self):
        ScraperRegistry.register(
            _fake_plugin(config="custom-feed.v2.json")
        )
        self.assertEqual(
            ScraperRegistry.get_plugin("fakestore").get_config_filename(),
            "custom-feed.v2.json",
        )

    def test_general_config_filename_is_reserved_case_insensitively(self):
        for filename in ("general.json", "GENERAL.JSON", "General.Json"):
            with self.subTest(filename=filename), self.assertRaises(PluginDiscoveryError):
                ScraperRegistry.register(_fake_plugin(config=filename))

    def test_config_filename_collision_is_case_insensitive_and_names_both_plugins(self):
        ScraperRegistry.register(_fake_plugin(
            name="first", domains=("first.example",), config="Feed.json"
        ))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(
                name="second", domains=("second.example",), config="feed.json"
            ))
        message = str(ctx.exception)
        self.assertIn("first", message)
        self.assertIn("second", message)
        self.assertIn("feed.json", message.lower())

    def test_equal_domain_conflict_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(name="first"))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(name="second"))
        self.assertIn("Domain conflict", str(ctx.exception))

    def test_nested_subdomain_conflict_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(name="first"))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(
                _fake_plugin(name="second", domains=("shop.fake-store.example",))
            )
        self.assertIn("Domain conflict", str(ctx.exception))

    def test_missing_base_setting_specs_is_rejected(self):
        # "return [my_spec]" instead of "BASE_SETTING_SPECS + [my_spec]" must fail
        # loudly at registration, not at the framework's first strict settings read.
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(specs=[]))
        self.assertIn("Extend, don't replace", str(ctx.exception))

    def test_non_canonical_cadence_is_rejected(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(
                _fake_plugin(directives={"OnCalendar": "*-*-* 03:00:00"})
            )
        self.assertIn("canonical cadences", str(ctx.exception))

    def test_unsafe_timer_directives_are_rejected(self):
        invalid = (
            {"OnCalendar": "hourly", "Bad-Key": "x"},
            {"OnCalendar": "hourly", "AccuracySec": "1m\nPersistent=false"},
            {"OnCalendar": "hourly", "AccuracySec": 60},
            [("OnCalendar", "hourly")],
        )
        for directives in invalid:
            with self.subTest(directives=directives):
                with self.assertRaises(PluginDiscoveryError) as ctx:
                    ScraperRegistry.register(
                        _fake_plugin(name="badtimer", directives=directives)
                    )
                self.assertIn("string-to-string mapping", str(ctx.exception))

    def test_rejected_plugin_is_not_registered(self):
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry.register(_fake_plugin(name="bad-name"))
        self.assertNotIn("bad-name", ScraperRegistry._plugins)

    def test_non_normalized_domain_still_routes(self):
        # matches_url folds each declared domain (strip + lower) the same way the
        # domain-conflict check does, so a padded/uppercase domain routes instead of
        # registering as a silently dead plugin.
        ScraperRegistry.register(_fake_plugin(domains=("CapStore.example ",)))
        plugin = ScraperRegistry.plugin_for_url("https://capstore.example/item/1")
        assert plugin is not None, "a non-normalized declared domain must still route"
        self.assertEqual(plugin.get_name(), "fakestore")

    def test_registration_freezes_cheap_metadata_and_defensively_copies_lists(self):
        domains = ["Store.Example."]
        specs = list(BASE_SETTING_SPECS)
        source = _fake_plugin(domains=domains, specs=specs)
        ScraperRegistry.register(source)
        frozen = ScraperRegistry.get_plugin("fakestore")

        domains[:] = ["mutated.example"]
        specs.clear()
        returned_domains = frozen.get_supported_domains()
        returned_domains.append("caller.example")
        returned_specs = frozen.get_setting_specs()
        returned_specs.clear()

        self.assertEqual(frozen.get_supported_domains(), ["store.example"])
        self.assertEqual(frozen.get_setting_specs(), BASE_SETTING_SPECS)
        self.assertIsNotNone(ScraperRegistry.plugin_for_url("https://store.example/p"))
        self.assertIsNone(ScraperRegistry.plugin_for_url("https://mutated.example/p"))

    def test_normalized_duplicate_domain_within_plugin_is_rejected(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(domains=("EXAMPLE.com", "example.com.")))
        self.assertIn("duplicate normalized domain", str(ctx.exception))

    def test_legacy_metadata_hooks_are_rejected_with_migration_guidance(self):
        class LegacyTimer(type(_fake_plugin())):
            def get_timer_directives(self):
                return {"OnCalendar": "daily"}

        class LegacyRequirements(type(_fake_plugin())):
            def get_requirements_path(self):
                return "/tmp/arbitrary.txt"

        for plugin, expected in ((LegacyTimer(), "get_default_interval"),
                                 (LegacyRequirements(), "beside plugin.py")):
            with self.subTest(expected=expected), self.assertRaises(PluginDiscoveryError) as ctx:
                ScraperRegistry.register(plugin)
            self.assertIn(expected, str(ctx.exception))

    def test_requirements_path_is_computed_from_plugin_source(self):
        from core.scrapers.skroutz import plugin as skroutz

        ScraperRegistry.register(skroutz)
        path = ScraperRegistry.get_requirements_path("skroutz")
        self.assertEqual(path, os.path.realpath(
            os.path.join(os.path.dirname(__file__), "../../src/core/scrapers/skroutz/requirements.txt")
        ))


class TestDiscoverPackageShape(unittest.TestCase):
    """discover()'s own package-level failure branches (before register() is reached).

    A plugin package that cannot be imported, exposes no module-level ``plugin``, or
    exposes a non-BasePlugin value must fail discovery loudly — these branches sit in
    front of the well-tested ``register()`` gate and would otherwise only be exercised
    by prose. ``pkgutil``/``importlib`` are mocked so the fake package needs no files.
    """

    def _discover(self, module=None, import_error=None):
        """Runs discovery over the one fake package; returns the resulting plugin dict
        (captured inside the sandbox, which restores the real registry on exit)."""
        import_mock = (mock.Mock(side_effect=import_error) if import_error
                       else mock.Mock(return_value=module))
        with registry_sandbox(frozen=False), \
             mock.patch("core.scrapers.registry.pkgutil.iter_modules",
                        return_value=[(None, "fakepkg", True)]), \
             mock.patch("core.scrapers.registry.importlib.import_module", import_mock):
            ScraperRegistry.discover()
            return dict(ScraperRegistry._plugins)

    def test_unimportable_package_fails_loudly(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            self._discover(import_error=ImportError("No module named 'heavy_lib'"))
        self.assertIn("Failed to import scraper plugin package", str(ctx.exception))
        self.assertIn("fakepkg", str(ctx.exception))

    def test_missing_plugin_attribute_fails_loudly(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            self._discover(module=types.SimpleNamespace())
        self.assertIn("does not expose", str(ctx.exception))
        self.assertIn("fakepkg", str(ctx.exception))

    def test_non_baseplugin_value_fails_loudly(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            self._discover(module=types.SimpleNamespace(plugin=object()))
        self.assertIn("not a BasePlugin instance", str(ctx.exception))

    def test_well_formed_package_registers(self):
        # The happy path through the same mocked discovery, so the failure tests
        # above can't pass merely because the mocking itself broke discovery.
        registered = self._discover(module=types.SimpleNamespace(plugin=_fake_plugin()))
        self.assertIn("fakestore", registered)


if __name__ == "__main__":
    unittest.main()
