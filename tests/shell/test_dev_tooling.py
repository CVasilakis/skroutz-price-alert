import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_plugin_check_help_needs_no_venv():
    result = _run("scripts/dev/plugin-check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: ./scripts/dev/plugin-check.sh --<target>" in result.stdout


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


def test_command_entrypoints_are_executable_and_libraries_are_not():
    commands = [
        ROOT / "install.sh",
        ROOT / "update.sh",
        *(ROOT / "scripts").glob("*.sh"),
        *(ROOT / "scripts/dev").glob("*.sh"),
        ROOT / ".githooks/pre-push",
    ]
    libraries = list((ROOT / "scripts/lib").glob("*.sh"))
    assert commands
    assert libraries
    assert all(os.access(path, os.X_OK) for path in commands)
    assert all(not os.access(path, os.X_OK) for path in libraries)


def test_update_help_runs_through_direct_executable_entrypoint():
    result = subprocess.run(
        [str(ROOT / "update.sh"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage: update.sh" in result.stdout


def test_shellcheck_ci_job_provisions_and_selects_supported_python():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    shellcheck_job = workflow.split("  shellcheck:\n", 1)[1].split("  typecheck:\n", 1)[0]
    assert "actions/setup-python@v6" in shellcheck_job
    assert "python-version: '3.10'" in shellcheck_job
    assert "pip install -r scripts/dev/requirements-shell.txt" in shellcheck_job
    assert "SCROOGE_CHECK_PYTHON=python" in shellcheck_job
    assert 'SCROOGE_SHELLCHECK="$(command -v shellcheck)"' in shellcheck_job


def test_indirect_signal_handler_has_cross_version_shellcheck_suppression():
    update = (ROOT / "update.sh").read_text(encoding="utf-8")
    assert "# shellcheck disable=SC2317,SC2329" in update


def test_shell_gate_checks_tracked_and_new_nonignored_scripts(tmp_path):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "check.sh", dev_scripts / "check.sh")
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "preflight.sh", lib / "preflight.sh")

    tracked = project / "tracked script.sh"
    new = project / "new script.sh"
    hook = project / ".githooks" / "pre-push"
    ignored = project / "ignored script.sh"
    deleted = project / "deleted script.sh"
    hook.parent.mkdir()
    for script in (tracked, new, hook, ignored, deleted):
        script.write_text("#!/bin/sh\n", encoding="utf-8")
    (project / ".gitignore").write_text("ignored script.sh\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        [
            "git",
            "add",
            ".gitignore",
            "scripts/dev/check.sh",
            "scripts/lib/common.sh",
            "scripts/lib/preflight.sh",
            tracked.name,
            str(hook.relative_to(project)),
            deleted.name,
        ],
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
    env["SCROOGE_CHECK_PYTHON"] = sys.executable
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
    assert "--exclude=SC2086,SC2046" not in arguments
    assert "scripts/dev/check.sh" in arguments
    assert tracked.name in arguments
    assert new.name in arguments
    assert str(hook.relative_to(project)) in arguments
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
    hooks = project / ".githooks"
    hooks.mkdir()
    shutil.copy(ROOT / ".githooks" / "pre-push", hooks / "pre-push")
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


def _hook_project(tmp_path: Path, hook_text: str | None):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "install-hooks.sh", dev_scripts / "install-hooks.sh")
    if hook_text is not None:
        hooks = project / ".githooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text(hook_text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    return project, dev_scripts / "install-hooks.sh"


def test_hook_installer_refuses_missing_hook_before_configuring(tmp_path):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "missing" in result.stderr
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        text=True,
        capture_output=True,
    )
    assert configured.returncode != 0


def test_hook_installer_refuses_invalid_hook_before_configuring(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nif\n")
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode != 0
    assert "invalid POSIX shell syntax" in result.stderr
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        text=True,
        capture_output=True,
    )
    assert configured.returncode != 0


def test_hook_installer_repairs_nonexecutable_hook(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    hook = project / ".githooks" / "pre-push"
    hook.chmod(0o644)
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert os.access(hook, os.X_OK)


def test_hook_installer_refuses_symlink_without_chmodding_target(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    hook = project / ".githooks" / "pre-push"
    external = tmp_path / "external-hook"
    external.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    external.chmod(0o644)
    hook.unlink()
    hook.symlink_to(external)

    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)

    assert result.returncode != 0
    assert external.stat().st_mode & 0o111 == 0


def test_versioned_pre_push_hook_runs_the_canonical_gate():
    hook = ROOT / ".githooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    assert 'exec "$PROJECT_ROOT/scripts/dev/check.sh"' in hook.read_text(encoding="utf-8")


def test_plugin_check_binds_static_analysis_to_selected_venv():
    contents = (ROOT / "scripts/dev/plugin-check.sh").read_text(encoding="utf-8")
    assert 'plugin_check_venv_parent="$(dirname -- "$plugin_check_venv_dir")"' in contents
    assert '-m basedpyright --venvpath "$plugin_check_venv_parent"' in contents
    check = (ROOT / "scripts/dev/check.sh").read_text(encoding="utf-8")
    assert "-m basedpyright src" in check
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'venvPath = "."' in pyproject
    assert 'venv = "venv"' in pyproject


def test_plugin_check_runs_from_external_venv_without_root_venv(tmp_path):
    project = tmp_path / "clean project"
    for relative in (
        "scripts/dev/plugin-check.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
        "pyproject.toml",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    shutil.copytree(ROOT / "src", project / "src")
    shutil.copytree(ROOT / "tests/plugins/insomnia", project / "tests/plugins/insomnia")

    external_venv = tmp_path / "isolation/venv"
    external_venv.parent.mkdir(parents=True)
    external_venv.symlink_to(sys.prefix, target_is_directory=True)
    external_python = external_venv / Path(sys.executable).relative_to(sys.prefix)
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CHECK_PYTHON"] = str(external_python)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/plugin-check.sh"), "--insomnia"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "venv").exists()


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


def test_dev_setup_rejects_project_venv_symlink_before_python_work(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    external = tmp_path / "external-venv"
    external.mkdir()
    (project / "venv").symlink_to(external, target_is_directory=True)

    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "must be a project-owned directory, not a symlink" in result.stderr
    assert (project / "venv").is_symlink()


@pytest.fixture
def python39(tmp_path):
    fake = tmp_path / "python3"
    fake.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) echo 3.9.18 ;;
            *) exit 1 ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


@pytest.mark.parametrize(
    ("script", "args", "override"),
    (
        ("scripts/dev/setup.sh", (), None),
        ("scripts/dev/plugin-create.sh", ("--help",), None),
        ("scripts/dev/check.sh", ("static",), "SCROOGE_CHECK_PYTHON"),
        ("scripts/dev/plugin-check.sh", ("--skroutz",), "SCROOGE_PLUGIN_CHECK_PYTHON"),
    ),
)
def test_development_wrappers_reject_python39(python39, script, args, override):
    env = os.environ.copy()
    env["PATH"] = f"{python39.parent}:{env['PATH']}"
    if override is not None:
        env[override] = str(python39)
    result = subprocess.run(
        ["sh", str(ROOT / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode != 0
    assert "Detected Python 3.9.18" in result.stderr
    assert "3.10 or newer" in result.stderr


def test_dev_setup_installs_core_dev_and_all_plugin_requirements(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/dev/install-hooks.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
        ".githooks/pre-push",
        "requirements.txt",
        "scripts/dev/requirements-dev.txt",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "pip-calls"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) echo 3.12.0 ;; esac
        exit 0 ;;
    -m)
        case "${2:-}" in
            core.scrapers.tooling.cli)
                printf 'alpha\\t%s\\nbeta\\t%s\\n' "$ALPHA_REQ" "$BETA_REQ" ;;
            venv)
                mkdir -p "$3/bin"
                cp "$0" "$3/bin/python3"
                chmod 755 "$3/bin/python3" ;;
            pip)
                printf '%s\\n' "$*" >> "$PIP_CAPTURE" ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    alpha = tmp_path / "alpha requirements.txt"
    beta = tmp_path / "beta requirements.txt"
    alpha.touch()
    beta.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ALPHA_REQ": str(alpha),
            "BETA_REQ": str(beta),
            "PIP_CAPTURE": str(capture),
        }
    )
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    calls = capture.read_text(encoding="utf-8")
    assert str(project / "requirements.txt") in calls
    assert str(project / "scripts/dev/requirements-dev.txt") in calls
    assert str(alpha) in calls
    assert str(beta) in calls


def test_dev_setup_rejects_existing_python39_venv_with_supported_system_python(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    system_python = fake_bin / "python3"
    system_python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) echo 3.12.0 ;; esac
        exit 0 ;;
esac
exit 99
""",
        encoding="utf-8",
    )
    system_python.chmod(0o755)
    venv_python = project / "venv/bin/python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) echo 3.9.18; exit 0 ;;
            *) exit 1 ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode != 0
    assert "Detected Python 3.9.18" in result.stderr


def test_developer_requirements_are_visible_without_unignoring_arbitrary_text():
    requirement_results = [
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", relative],
            cwd=ROOT,
        )
        for relative in (
            "scripts/dev/requirements-dev.txt",
            "scripts/dev/requirements-shell.txt",
        )
    ]
    arbitrary_text = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "scripts/dev/notes.txt"],
        cwd=ROOT,
    )

    assert all(result.returncode == 1 for result in requirement_results)
    assert arbitrary_text.returncode == 0
