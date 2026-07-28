import json
from pathlib import Path
from unittest import mock

import pytest

from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.tooling.scaffold import ScaffoldRequest, create_plugin, main, validate_request

REQUEST = ScaffoldRequest("acme_store", "Acme Store", "Store.Example", "/products")


def test_scaffold_creates_only_additive_source_and_test_packages(tmp_path):
    sentinel = tmp_path / "README.md"
    sentinel.write_text("untouched", encoding="utf-8")

    source, tests = create_plugin(tmp_path, REQUEST)

    assert sentinel.read_text(encoding="utf-8") == "untouched"
    assert source == tmp_path / "src/core/scrapers/plugins/acme_store"
    assert tests == tmp_path / "tests/plugins/acme_store"
    assert {path.name for path in source.iterdir()} == {
        "__init__.py",
        "plugin.py",
        "client.py",
        "README.md",
        "config.example.json",
    }
    assert {path.name for path in tests.iterdir()} == {"__init__.py", "test_client.py"}
    document = json.loads((source / "config.example.json").read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["plugin_schema_version"] == 1
    assert document["items"][0]["url"] == "https://store.example/products/sample"
    assert "pytest.fail" in (tests / "test_client.py").read_text(encoding="utf-8")


def test_scaffold_output_is_discoverable_and_example_loads(tmp_path):
    import core.scrapers.plugins as plugin_package

    source, _tests = create_plugin(tmp_path, REQUEST)
    discovery_root = source.parent
    saved_path = list(plugin_package.__path__)
    plugin_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers.plugins")
        plugin = catalog.get("acme_store")
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / plugin.config_filename).write_bytes(
            (source / "config.example.json").read_bytes()
        )
        loaded = TargetConfigLoader(plugin, str(config_dir)).load()
        assert not loaded.row_issues
        assert len(loaded.items) == 1
    finally:
        plugin_package.__path__[:] = saved_path


@pytest.mark.parametrize(
    "scaffold_request",
    [
        ScaffoldRequest("Bad", "Acme", "store.example", "/products/"),
        ScaffoldRequest("help", "Acme", "store.example", "/products/"),
        ScaffoldRequest("acme", " ", "store.example", "/products/"),
        ScaffoldRequest("acme", "Acme", "https://store.example", "/products/"),
        ScaffoldRequest("acme", "Acme", "store.example", "products/"),
        ScaffoldRequest("acme", "Acme", "store.example", "/products/?q=x"),
    ],
)
def test_scaffold_rejects_invalid_identity_and_url_inputs(scaffold_request):
    with pytest.raises(ValueError):
        validate_request(scaffold_request)


def test_scaffold_refuses_collisions_without_touching_other_destination(tmp_path):
    source = tmp_path / "src/core/scrapers/plugins/acme_store"
    source.mkdir(parents=True)
    marker = source / "keep.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        create_plugin(tmp_path, REQUEST)

    assert marker.read_text(encoding="utf-8") == "keep"
    assert not (tmp_path / "tests/plugins/acme_store").exists()


def test_scaffold_rolls_back_both_new_directories_after_partial_failure(tmp_path):
    from core.scrapers.tooling import scaffold

    real_write = scaffold._write_tree
    calls = 0

    def failing_second_write(root: Path, files: dict[str, str]) -> None:
        nonlocal calls
        calls += 1
        real_write(root, files)
        if calls == 2:
            raise OSError("disk full")

    with mock.patch.object(scaffold, "_write_tree", side_effect=failing_second_write):
        with pytest.raises(OSError, match="disk full"):
            create_plugin(tmp_path, REQUEST)

    assert not (tmp_path / "src/core/scrapers/plugins/acme_store").exists()
    assert not (tmp_path / "tests/plugins/acme_store").exists()


def test_scaffold_cli_reports_success_and_collision(tmp_path, capsys):
    args = [
        "acme_store",
        "--display-name",
        "Acme Store",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--repo-root",
        str(tmp_path),
    ]
    assert main(args) == 0
    assert "./scripts/dev/plugin-check.sh --acme_store" in capsys.readouterr().out
    assert main(args) == 1
    assert "refusing to overwrite" in capsys.readouterr().err


def test_scaffold_cli_shell_output_is_structured_and_hidden_from_help(tmp_path, capsys):
    args = [
        "acme_store",
        "--display-name",
        "Acme Store",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--repo-root",
        str(tmp_path),
        "--shell-output",
    ]

    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == "scaffold\t1\tacme_store\n"
    assert captured.err == ""

    with pytest.raises(SystemExit, match="0"):
        main(["--help"])
    assert "--shell-output" not in capsys.readouterr().out


def test_scaffold_cli_shell_output_uses_validated_target(tmp_path, capsys):
    args = [
        " acme_store ",
        "--display-name",
        "Acme Store",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--repo-root",
        str(tmp_path),
        "--shell-output",
    ]

    assert main(args) == 0
    assert capsys.readouterr().out == "scaffold\t1\tacme_store\n"
