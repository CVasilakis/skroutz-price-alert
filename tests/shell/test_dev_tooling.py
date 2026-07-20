import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run(script: str, *args: str):
    return subprocess.run(
        ["sh", str(ROOT / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=os.environ.copy(),
    )


def test_plugin_create_help_needs_no_venv_and_has_required_inputs():
    result = _run("scripts/plugin-create.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "--display-name" in result.stdout
    assert "--domain" in result.stdout
    assert "--url-prefix" in result.stdout


def test_dev_setup_help_has_no_install_side_effect():
    result = _run("scripts/dev-setup.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "without systemd" in result.stdout


def test_dev_setup_contains_no_service_or_user_data_operations():
    contents = (ROOT / "scripts/dev-setup.sh").read_text(encoding="utf-8")
    assert "systemctl" not in contents
    assert "config/" not in contents
    assert "state/" not in contents


def test_plugin_check_binds_static_analysis_to_selected_venv():
    contents = (ROOT / "scripts/plugin-check.sh").read_text(encoding="utf-8")
    assert '-m basedpyright --venvpath "$plugin_venv_parent"' in contents


def test_dev_setup_rejects_invalid_argument_before_installing():
    result = _run("scripts/dev-setup.sh", "target")
    assert result.returncode == 1
    assert "Invalid argument" in result.stderr


def test_dev_setup_rejects_multiple_targets_before_installing():
    result = _run("scripts/dev-setup.sh", "--skroutz", "--insomnia")
    assert result.returncode == 1
    assert "at most one" in result.stderr


def test_dev_setup_rejects_unknown_target_before_pip_work():
    result = _run("scripts/dev-setup.sh", "--does_not_exist")
    assert result.returncode == 1
    assert "Unknown target 'does_not_exist'" in result.stderr
    assert "Requirement already satisfied" not in result.stdout
