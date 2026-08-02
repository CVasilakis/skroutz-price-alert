import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.tooling.scaffold import (
    CustomValueSpec,
    ScaffoldRequest,
    _json_value,
    create_plugin,
    main,
    validate_request,
)

REQUEST = ScaffoldRequest("acme_store", "Acme Store", ("Store.Example",), "/products")


def test_scaffold_creates_only_additive_source_and_test_packages(tmp_path):
    sentinel = tmp_path / "README.md"
    sentinel.write_text("untouched", encoding="utf-8")

    result = create_plugin(tmp_path, REQUEST)
    source, tests = result.source, result.tests

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
    generated_test = (tests / "test_client.py").read_text(encoding="utf-8")
    generated_readme = (source / "README.md").read_text(encoding="utf-8")
    assert "pytest.skip" in generated_test
    assert "SCROOGE_SCAFFOLD_TODO" in generated_test
    assert "from support import decode_test_config" in generated_test
    assert "values.items[0][URL]" in generated_test
    assert "_custom" not in generated_test
    assert "core.settings" not in generated_test
    assert "core.exceptions" not in generated_test
    assert "core.scrapers.framework" not in generated_test
    assert "./scripts/dev/plugin-check.sh --acme_store" in generated_readme
    assert "./scripts/dev/check.sh --debug" in generated_readme
    assert "../../../../../CONTRIBUTING.md" in generated_readme


def test_scaffold_output_is_discoverable_and_example_loads(tmp_path):
    import core.scrapers.plugins as plugin_package

    source = create_plugin(tmp_path, REQUEST).source
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
        ScaffoldRequest("Bad", "Acme", ("store.example",), "/products/"),
        ScaffoldRequest("help", "Acme", ("store.example",), "/products/"),
        ScaffoldRequest("migration", "Acme", ("store.example",), "/products/"),
        ScaffoldRequest("reminder", "Acme", ("store.example",), "/products/"),
        ScaffoldRequest("acme", " ", ("store.example",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("https://store.example",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("store.example:443",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("user@store.example",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("*.store.example",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("store.example/path",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("store.example?q=x",), "/products/"),
        ScaffoldRequest("acme", "Acme", ("store.example",), "products/"),
        ScaffoldRequest("acme", "Acme", ("store.example",), "/product pages/"),
        ScaffoldRequest("acme", "Acme", ("store.example",), "/products/?q=x"),
        ScaffoldRequest("acme", "Acme", ("store.example",), "/products/#details"),
    ],
)
def test_scaffold_rejects_invalid_identity_and_url_inputs(scaffold_request):
    with pytest.raises(ValueError):
        validate_request(scaffold_request)


@pytest.mark.parametrize(
    ("target", "message"),
    [
        ("HAHA", "must use lowercase letters; try 'haha' instead of 'HAHA'"),
        ("1store", "must begin with a lowercase letter"),
        ("acme-store", "use underscores between words"),
        ("help", "is reserved; choose a store-specific name"),
        ("status", "matches a Scrooge Alert command"),
    ],
)
def test_scaffold_target_errors_explain_how_to_correct_the_name(target, message):
    request = ScaffoldRequest(target, "Acme", ("store.example",), "/products/")

    with pytest.raises(ValueError, match=message):
        validate_request(request)


@pytest.mark.parametrize(
    ("value_type", "example", "message"),
    [
        ("text", "", "nonblank string"),
        ("integer", True, "integer"),
        ("number", float("inf"), "finite"),
        ("nonnegative-number", -1, "non-negative"),
        ("boolean", "haha", "boolean"),
        ("text-list", ["valid", ""], "array of nonblank strings"),
        ("unknown", "value", "type must be one of"),
    ],
)
def test_scaffold_rejects_examples_that_do_not_match_the_declared_type(
    value_type, example, message
):
    request = ScaffoldRequest(
        "acme",
        "Acme",
        ("store.example",),
        "/products/",
        item_fields=(CustomValueSpec("custom_value", value_type, example),),
    )

    with pytest.raises(ValueError, match=message):
        validate_request(request)


@pytest.mark.parametrize(
    ("value_type", "example", "expected"),
    [
        ("text", "  value  ", "value"),
        ("integer", 2, 2),
        ("number", 2.5, 2.5),
        ("nonnegative-number", 0, 0.0),
        ("boolean", False, False),
        ("text-list", [" one ", "two"], ("one", "two")),
    ],
)
def test_scaffold_accepts_and_normalizes_every_declared_value_type(value_type, example, expected):
    request = ScaffoldRequest(
        "acme",
        "Acme",
        ("store.example",),
        "/products/",
        item_fields=(CustomValueSpec("custom_value", value_type, example),),
    )

    validated = validate_request(request)

    assert validated.item_fields[0].example == expected


@pytest.mark.parametrize(
    ("value_type", "example", "invalid_default", "message"),
    [
        ("text", "valid", "", "nonblank string"),
        ("integer", 1, True, "integer"),
        ("number", 1, float("inf"), "finite"),
        ("nonnegative-number", 1, -1, "non-negative"),
        ("boolean", True, "haha", "boolean"),
        ("text-list", ["valid"], [""], "array of nonblank strings"),
    ],
)
def test_scaffold_rejects_invalid_optional_defaults_for_every_declared_type(
    value_type, example, invalid_default, message
):
    request = ScaffoldRequest(
        "acme",
        "Acme",
        ("store.example",),
        "/products/",
        settings=(CustomValueSpec("custom_setting", value_type, example, invalid_default),),
    )

    with pytest.raises(ValueError, match=message):
        validate_request(request)


@pytest.mark.parametrize(
    "raw",
    ["'value'", "True", "None", '["value",]', "NaN", "Infinity", "-Infinity"],
)
def test_automation_arguments_reject_values_outside_strict_json(raw):
    with pytest.raises(ValueError, match="field example must be valid JSON"):
        _json_value(raw, context="field example")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"value"', "value"),
        ("2", 2),
        ("2.5", 2.5),
        ("false", False),
        ('["value"]', ["value"]),
        ("null", None),
    ],
)
def test_automation_arguments_accept_standard_json_values(raw, expected):
    assert _json_value(raw, context="field example") == expected


def test_scaffold_cli_rejects_nonfinite_json_before_creating_files(tmp_path, capsys):
    args = [
        "acme",
        "--display-name",
        "Acme",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--result-type",
        "price",
        "--default-interval",
        "1h",
        "--transport",
        "bare",
        "--with-tests",
        "--required-item-field",
        "score",
        "number",
        "NaN",
        "--repo-root",
        str(tmp_path),
    ]

    assert main(args) == 1
    assert "NaN is not permitted by strict JSON" in capsys.readouterr().err
    assert not (tmp_path / "src/core/scrapers/plugins/acme").exists()


@pytest.mark.parametrize(
    ("item_fields", "settings", "message"),
    [
        ((CustomValueSpec("url", "text", "value"),), (), "item field key 'url'"),
        (
            (),
            (CustomValueSpec("execution_interval", "text", "1h"),),
            "setting key 'execution_interval'",
        ),
        (
            (
                CustomValueSpec("region", "text", "eu"),
                CustomValueSpec("region", "text", "us"),
            ),
            (),
            "duplicate item field key 'region'",
        ),
    ],
)
def test_scaffold_rejects_reserved_and_duplicate_custom_keys(item_fields, settings, message):
    request = ScaffoldRequest(
        "acme",
        "Acme",
        ("store.example",),
        "/products/",
        item_fields=item_fields,
        settings=settings,
    )

    with pytest.raises(ValueError, match=message):
        validate_request(request)


@pytest.mark.parametrize("target", ["ping", "status"])
def test_scaffold_rejects_command_names_as_targets(target):
    request = ScaffoldRequest(target, target.title(), (f"{target}.example",), "/products/")

    with pytest.raises(ValueError, match="matches a Scrooge Alert command"):
        validate_request(request)


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
        "--result-type",
        "price",
        "--default-interval",
        "1h",
        "--transport",
        "bare",
        "--with-tests",
        "--repo-root",
        str(tmp_path),
    ]
    assert main(args) == 0
    output = capsys.readouterr().out
    assert "./scripts/dev/plugin-check.sh --acme_store" in output
    assert "./scripts/dev/check.sh --debug" in output
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
        "--result-type",
        "price",
        "--default-interval",
        "1h",
        "--transport",
        "bare",
        "--with-tests",
        "--repo-root",
        str(tmp_path),
        "--shell-output",
    ]

    assert main(args) == 0
    captured = capsys.readouterr()
    assert captured.out == "scaffold\t1\tacme_store\t1\n"
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
        "--result-type",
        "price",
        "--default-interval",
        "1h",
        "--transport",
        "bare",
        "--with-tests",
        "--repo-root",
        str(tmp_path),
        "--shell-output",
    ]

    assert main(args) == 0
    assert capsys.readouterr().out == "scaffold\t1\tacme_store\t1\n"


def test_scaffold_generates_listing_http_custom_contract_without_tests(tmp_path):
    request = ScaffoldRequest(
        "market_watch",
        "Market Watch",
        ("market.example", "shop.example"),
        "/listings/",
        result_type="listing",
        transport="http",
        item_fields=(CustomValueSpec("title_terms", "text-list", ("Pixel",), ()),),
        settings=(CustomValueSpec("api_token", "text", "example-token", sensitive=True),),
        dependencies=("beautifulsoup4",),
        include_tests=False,
    )

    result = create_plugin(tmp_path, request)

    assert result.tests is None
    assert not (tmp_path / "tests/plugins/market_watch").exists()
    descriptor = (result.source / "plugin.py").read_text(encoding="utf-8")
    client = (result.source / "client.py").read_text(encoding="utf-8")
    requirements = (result.source / "requirements.txt").read_text(encoding="utf-8")
    assert "ListingResult" in client
    assert "HttpScraperClient" in client
    assert "ITEM_TITLE_TERMS" in descriptor
    assert "SETTING_API_TOKEN" in descriptor
    assert "sensitive=True" in descriptor
    assert requirements.splitlines() == ["tls-client", "beautifulsoup4"]


def test_fresh_scaffold_passes_ruff_and_basedpyright(tmp_path):
    result = create_plugin(tmp_path, REQUEST)
    assert result.tests is not None
    paths = (str(result.source), str(result.tests))

    subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--config",
            'lint.isort.known-first-party=["core"]',
            *paths,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--check", *paths],
        check=True,
        capture_output=True,
        text=True,
    )

    repo_root = Path(__file__).resolve().parents[4]
    pyright_config = tmp_path / "pyrightconfig.json"
    pyright_config.write_text(
        json.dumps(
            {
                "include": [str(result.source)],
                "extraPaths": [str(repo_root / "src"), str(tmp_path / "src")],
                "pythonVersion": "3.10",
                "typeCheckingMode": "standard",
            }
        ),
        encoding="utf-8",
    )
    pyright = subprocess.run(
        [sys.executable, "-m", "basedpyright", "--project", str(pyright_config)],
        capture_output=True,
        text=True,
    )
    assert pyright.returncode == 0, pyright.stdout + pyright.stderr
