import subprocess
from pathlib import Path

from core.scrapers.tooling.scaffold import ScaffoldRequest, create_plugin

ROOT = Path(__file__).resolve().parents[2]
FULL_GATE = "./scripts/dev/check.sh --debug"


def _scaffold_layout(root: Path) -> None:
    (root / "src/core/scrapers/plugins").mkdir(parents=True)
    (root / "tests/plugins").mkdir(parents=True)


def test_coverage_reporting_has_no_failure_threshold() -> None:
    """Coverage visibility must never become a percentage-based test gate."""
    threshold_options = ("cov-" + "fail-under", "fail" + "_under")
    conventional_test_configs = (
        ROOT / "pyproject.toml",
        ROOT / "pytest.ini",
        ROOT / "setup.cfg",
        ROOT / "tox.ini",
        ROOT / ".coveragerc",
    )
    workflow_configs = tuple((ROOT / ".github" / "workflows").glob("*.y*ml"))
    configured_test_runners = (
        path for path in (*conventional_test_configs, *workflow_configs) if path.exists()
    )

    for path in configured_test_runners:
        contents = path.read_text(encoding="utf-8")
        active_configuration = "\n".join(
            line for line in contents.splitlines() if not line.lstrip().startswith("#")
        )
        for option in threshold_options:
            assert option not in active_configuration, (
                f"remove coverage threshold {option!r} from {path}"
            )


def test_plugin_artifacts_and_ci_use_the_current_package_layout() -> None:
    """Contributor files remain visible to Git and CI resolves the tooling package."""
    plugin_root = "src/core/scrapers/plugins"
    for relative_path in (
        f"{plugin_root}/new_store/config.example.json",
        f"{plugin_root}/new_store/requirements.txt",
    ):
        result = subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", relative_path],
            cwd=ROOT,
            check=False,
        )
        assert result.returncode == 1, f"plugin contributor file is ignored: {relative_path}"

    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert f"{plugin_root}/*/requirements.txt" in workflow
    assert "core.scrapers.tooling.cli requirements" in workflow
    assert "src/core/scrapers/" + "*/requirements.txt" not in workflow
    assert "core.scrapers." + "cli requirements" not in workflow


def test_contributor_surfaces_share_the_required_commands(tmp_path) -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    pull_request = (ROOT / ".github/pull_request_template.md").read_text(encoding="utf-8")
    _scaffold_layout(tmp_path)
    source = create_plugin(
        tmp_path,
        ScaffoldRequest("acme_store", "Acme Store", ("store.example",), "/items/"),
    ).source
    generated = (source / "README.md").read_text(encoding="utf-8")

    for name, surface in {
        "contributor guide": contributing,
        "generated README": generated,
        "pull request template": pull_request,
    }.items():
        assert FULL_GATE in surface, f"{name} must recommend the complete debug gate"
        assert "`./scripts/dev/check.sh`" not in surface, (
            f"{name} recommends the incomplete non-debug gate"
        )

    assert "./scripts/dev/plugin-check.sh --acme_store" in generated
    assert "CONTRIBUTING.md" in generated
    assert "supported inputs or URL shapes" in contributing
    assert "docstring is fine" in contributing


def test_contributor_guide_owns_advanced_migration_details(tmp_path) -> None:
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    _scaffold_layout(tmp_path)
    source = create_plugin(
        tmp_path,
        ScaffoldRequest("acme_store", "Acme Store", ("store.example",), "/items/"),
    ).source
    generated = (source / "README.md").read_text(encoding="utf-8")

    assert "from core.scrapers.api import JsonObject" in contributing
    assert "config_schema_version" in contributing
    assert "plugin_schema_version" in contributing
    assert "CONFIG_MIGRATIONS" in contributing
    assert "migrations" in generated
    assert "CONTRIBUTING.md" in generated
    assert "core.infrastructure.migration" not in contributing
