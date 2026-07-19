import shutil
import sys
from pathlib import Path

import pytest

from core.scrapers.check import (
    _check_contributor_files,
    _check_self_contained,
    check_plugin,
)
from core.scrapers.registry import PluginCatalog


def test_contributor_files_require_target_owned_tests(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("guide", encoding="utf-8")
    (source / "config.example.json").write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="tests/plugins/acme/test_"):
        _check_contributor_files(source, tmp_path / "missing-tests", "acme")


def test_self_contained_check_rejects_sibling_plugin_import(tmp_path):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "client.py").write_text(
        "from core.scrapers.other_store.client import Client\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="sibling plugin 'other_store'"):
        _check_self_contained(source, "acme", frozenset({"acme", "other_store"}))


def test_verifier_rejects_an_empty_example_config(tmp_path):
    import core.scrapers as scraper_package

    discovery_root = tmp_path / "core" / "scrapers"
    target_dir = discovery_root / "empty_store"
    shutil.copytree(Path("src/core/scrapers/_example"), target_dir)
    example = target_dir / "config.example.json"
    example.write_text(
        '{"settings":{"execution_interval":"1h"},"items":[]}', encoding="utf-8"
    )
    tests = tmp_path / "tests" / "plugins" / "empty_store"
    tests.mkdir(parents=True)
    (tests / "test_client.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")

    saved_path = list(scraper_package.__path__)
    scraper_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers")
        with pytest.raises(RuntimeError, match="at least one valid item"):
            check_plugin("empty_store", catalog, repo_root=tmp_path)
    finally:
        scraper_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.empty_store" or name.startswith(
                "core.scrapers.empty_store."
            ):
                sys.modules.pop(name, None)


@pytest.mark.parametrize(
    "settings,item,expected",
    [
        ({}, {"tag": "featured"}, "custom setting 'region'"),
        ({"region": "global"}, {}, "custom item field 'tag'"),
    ],
)
def test_verifier_requires_examples_to_demonstrate_custom_schema(
    tmp_path, settings, item, expected
):
    import core.scrapers as scraper_package

    discovery_root = tmp_path / "core" / "scrapers"
    source = discovery_root / "custom_store"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "plugin.py").write_text(
        '''from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec
TAG = ItemField("tag", str, "plain")
REGION = SettingSpec("region", "global", str)
PLUGIN = ScraperPlugin(
    display_name="Custom Store", domains=("store.example",),
    accepts_url=lambda url: url.path.startswith("/products/"),
    item_fields=(TAG,), settings=(REGION,),
)
''',
        encoding="utf-8",
    )
    (source / "client.py").write_text(
        '''from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        return PriceResult(1, "EUR")
''',
        encoding="utf-8",
    )
    (source / "README.md").write_text(
        "Accepts product URLs and returns PriceResult. Custom keys: tag, region.",
        encoding="utf-8",
    )
    document = {
        "settings": settings,
        "items": [{
            "id": "one", "name": "One", "url": "https://store.example/products/1",
            "target_price": 2, **item,
        }],
    }
    import json
    (source / "config.example.json").write_text(json.dumps(document), encoding="utf-8")
    tests = tmp_path / "tests" / "plugins" / "custom_store"
    tests.mkdir(parents=True)
    (tests / "test_client.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")

    saved_path = list(scraper_package.__path__)
    scraper_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers")
        with pytest.raises(RuntimeError, match=expected):
            check_plugin("custom_store", catalog, repo_root=tmp_path)
    finally:
        scraper_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.custom_store" or name.startswith(
                "core.scrapers.custom_store."
            ):
                sys.modules.pop(name, None)
