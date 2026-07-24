import os
import shutil
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
    result = _run("scripts/dev/plugin-create.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "--display-name" in result.stdout
    assert "--domain" in result.stdout
    assert "--url-prefix" in result.stdout


def test_dev_setup_help_has_no_install_side_effect():
    result = _run("scripts/dev/setup.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "without systemd" in result.stdout


def test_check_help_needs_no_venv_and_describes_full_gate():
    result = _run("scripts/dev/check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "complete local pre-push gate" in result.stdout


def test_check_rejects_unknown_mode_before_running_tools():
    result = _run("scripts/dev/check.sh", "unknown")
    assert result.returncode == 2
    assert "Invalid argument" in result.stderr


def test_shell_gate_checks_tracked_and_new_nonignored_scripts(tmp_path):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "check.sh", dev_scripts / "check.sh")

    tracked = project / "tracked script.sh"
    new = project / "new script.sh"
    ignored = project / "ignored script.sh"
    deleted = project / "deleted script.sh"
    for script in (tracked, new, ignored, deleted):
        script.write_text("#!/bin/sh\n", encoding="utf-8")
    (project / ".gitignore").write_text("ignored script.sh\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        ["git", "add", ".gitignore", "scripts/dev/check.sh", tracked.name, deleted.name],
        cwd=project,
        check=True,
    )
    deleted.unlink()

    capture = tmp_path / "shellcheck-arguments.txt"
    fake_shellcheck = tmp_path / "shellcheck"
    fake_shellcheck.write_text(
        '#!/bin/sh\nset -eu\nprintf "%s\\n" "$@" >> "$SHELLCHECK_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_shellcheck.chmod(0o755)

    env = os.environ.copy()
    env["SCROOGE_SHELLCHECK"] = str(fake_shellcheck)
    env["SHELLCHECK_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["sh", str(dev_scripts / "check.sh"), "shell"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert "-x" in arguments
    assert "--exclude=SC2086,SC2046" in arguments
    assert "scripts/dev/check.sh" in arguments
    assert tracked.name in arguments
    assert new.name in arguments
    assert ignored.name not in arguments
    assert deleted.name not in arguments


def test_dev_setup_contains_no_service_or_user_data_operations():
    contents = (ROOT / "scripts/dev/setup.sh").read_text(encoding="utf-8")
    assert "systemctl" not in contents
    assert "config/" not in contents
    assert "state/" not in contents
    assert '"$PROJECT_ROOT/scripts/dev/install-hooks.sh"' in contents


def test_dependency_installers_upgrade_all_requirement_sets_on_rerun():
    install = (ROOT / "install.sh").read_text(encoding="utf-8")
    setup = (ROOT / "scripts/dev/setup.sh").read_text(encoding="utf-8")

    assert '-m pip install -q --upgrade -r "$REQUIREMENTS_FILE"' in install
    assert '-m pip install -q --upgrade -r "$req_path"' in install
    assert '-m pip install --upgrade -r "$PROJECT_ROOT/requirements.txt"' in setup
    assert '-m pip install --upgrade -r "$requirement"' in setup


def test_hook_installer_sets_only_repository_local_hooks_path(tmp_path):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(
        ROOT / "scripts" / "dev" / "install-hooks.sh",
        dev_scripts / "install-hooks.sh",
    )
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    result = subprocess.run(
        ["sh", str(dev_scripts / "install-hooks.sh")],
        cwd=project,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert configured.stdout.strip() == ".githooks"


def test_versioned_pre_push_hook_runs_the_canonical_gate():
    hook = ROOT / ".githooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    assert 'exec "$PROJECT_ROOT/scripts/dev/check.sh"' in hook.read_text(encoding="utf-8")


def test_plugin_check_binds_static_analysis_to_selected_venv():
    contents = (ROOT / "scripts/dev/plugin-check.sh").read_text(encoding="utf-8")
    assert '-m basedpyright --venvpath "$plugin_venv_parent"' in contents


def test_dev_setup_rejects_invalid_argument_before_installing():
    result = _run("scripts/dev/setup.sh", "target")
    assert result.returncode == 1
    assert "Invalid argument" in result.stderr


def test_dev_setup_rejects_multiple_targets_before_installing():
    result = _run("scripts/dev/setup.sh", "--skroutz", "--insomnia")
    assert result.returncode == 1
    assert "at most one" in result.stderr


def test_dev_setup_rejects_unknown_target_before_pip_work():
    result = _run("scripts/dev/setup.sh", "--does_not_exist")
    assert result.returncode == 1
    assert "Unknown target 'does_not_exist'" in result.stderr
    assert "Requirement already satisfied" not in result.stdout


def test_developer_requirements_are_visible_without_unignoring_arbitrary_text():
    developer_requirements = subprocess.run(
        [
            "git",
            "check-ignore",
            "--quiet",
            "--no-index",
            "scripts/dev/requirements-dev.txt",
        ],
        cwd=ROOT,
    )
    arbitrary_text = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "scripts/dev/notes.txt"],
        cwd=ROOT,
    )

    assert developer_requirements.returncode == 1
    assert arbitrary_text.returncode == 0
