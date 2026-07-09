"""Per-plugin contract battery, parametrized over every registered plugin.

A new store gets this whole battery for free the moment its package is
discoverable — no new test files needed. It guards the obligations the registry's
discovery-time validation deliberately does *not* check (because they would need
file I/O or the plugin's deferred imports):

* the ``config/<config filename>.example`` template exists (``install.sh`` and
  ``schedule.sh`` tell users to copy it),
* that template actually loads through the plugin's real storage class, with no
  faulty rows, every row parseable, and a save round-trip that persists,
* every example row's URL routes back to the owning plugin, and
* the bound client class resolves to a ``BaseScraperClient`` subclass.

Checks that need the plugin's (possibly uninstalled) dependencies skip cleanly,
mirroring the production ``PluginDependencyError`` behavior of a partial install.

A plugin only needs a test module of its own for store-specific logic (URL-path
rules, extra model fields, parsing); model it on ``tests/unit/test_skroutz_storage.py``.
"""

import datetime
import os
import shutil

import pytest

from core.constants import CONFIG_DIR, TIMESTAMP_FORMAT
from core.exceptions import PluginDependencyError
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.registry import ScraperRegistry

TARGETS = ScraperRegistry.registered_targets()


def test_discovery_registered_at_least_one_plugin():
    """The silent-green guard for the whole battery below.

    Every other test here is parametrized over TARGETS; if discovery ever came
    back empty (a broken registry, a collection-order accident), they would all
    collect to zero tests and the battery would pass green while checking
    nothing. This makes that state a loud failure instead.
    """
    assert TARGETS, "plugin discovery returned no targets — the contract battery ran on nothing"


def _example_path(target: str) -> str:
    filename = ScraperRegistry.get_plugin(target).get_config_filename()
    return os.path.join(CONFIG_DIR, filename + ".example")


def _manager_on_example(target, tmp_path):
    """Copies the plugin's example config to a temp dir and loads it through the
    plugin's real storage class (skipping if its dependencies are not installed)."""
    plugin = ScraperRegistry.get_plugin(target)
    shutil.copy(_example_path(target), tmp_path / plugin.get_config_filename())
    registry = ScraperRegistry(str(tmp_path))
    try:
        manager = registry.get_manager(target)
    except PluginDependencyError as e:
        pytest.skip(str(e))
    manager.load()
    return plugin, manager


@pytest.mark.parametrize("target", TARGETS)
def test_ships_example_config(target):
    filename = ScraperRegistry.get_plugin(target).get_config_filename()
    assert os.path.isfile(_example_path(target)), (
        f"Plugin '{target}' declares config '{filename}' but ships no "
        f"config/{filename}.example template - install.sh and schedule.sh "
        f"tell users to copy it."
    )


@pytest.mark.parametrize("target", TARGETS)
def test_example_config_loads_cleanly(target, tmp_path):
    _, manager = _manager_on_example(target, tmp_path)
    assert manager.get_item_count() > 0, "the example template should show real rows"
    assert manager.get_faulty_indices() == [], "a shipped template must have no faulty rows"
    for row in manager.get_items():
        item = manager.parse_item(row)
        assert item.name, f"row {row!r} parsed to an unnamed item"


@pytest.mark.parametrize("target", TARGETS)
def test_example_urls_route_back_to_the_plugin(target, tmp_path):
    plugin, manager = _manager_on_example(target, tmp_path)
    for row in manager.get_items():
        assert plugin.matches_url(row["url"]), (
            f"example URL {row['url']} does not route back to plugin '{target}'"
        )


@pytest.mark.parametrize("target", TARGETS)
def test_example_config_round_trips_a_save(target, tmp_path):
    _, manager = _manager_on_example(target, tmp_path)
    url = manager.get_items()[0]["url"]
    stamp = datetime.datetime(2026, 1, 1).strftime(TIMESTAMP_FORMAT)
    manager.update_item(url, last_price=123.45, last_checked=stamp)
    manager.save()

    reloaded = ScraperRegistry(str(tmp_path)).get_manager(target)
    reloaded.load()
    row = next(r for r in reloaded.get_items() if r["url"] == url)
    assert row["last_price"] == 123.45
    assert row["last_checked"] == stamp


@pytest.mark.parametrize("target", TARGETS)
def test_client_class_resolves_to_the_base_contract(target):
    plugin = ScraperRegistry.get_plugin(target)
    try:
        client_cls = plugin.get_client_class()
    except ImportError as e:
        pytest.skip(f"'{target}' dependencies not installed ({e})")
    assert isinstance(client_cls, type) and issubclass(client_cls, BaseScraperClient), (
        f"get_client_class() of '{target}' must return a BaseScraperClient subclass"
    )
