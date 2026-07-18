from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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
