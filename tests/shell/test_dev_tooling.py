import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.scrapers.framework.catalog import PluginCatalog
from shell.assertions import assert_task_status

ROOT = Path(__file__).resolve().parents[2]

HELP_SCRIPTS = (
    "install.sh",
    "update.sh",
    "scripts/run.sh",
    "scripts/stop.sh",
    "scripts/disable.sh",
    "scripts/enable.sh",
    "scripts/schedule.sh",
    "scripts/uninstall.sh",
    "scripts/migrate.sh",
    "scripts/dev/setup.sh",
    "scripts/dev/install-hooks.sh",
    "scripts/dev/check.sh",
    "scripts/dev/plugin-check.sh",
    "scripts/dev/plugin-create.sh",
)


def _run(script: str, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        ["sh", str(ROOT / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env or os.environ.copy(),
    )


def test_plugin_create_help_needs_no_venv_and_has_required_inputs():
    result = _run("scripts/dev/plugin-create.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "--display-name" in result.stdout
    assert "--domain" in result.stdout
    assert "--url-prefix" in result.stdout
    assert "--debug" in result.stdout


def _plugin_create_args(repo_root: Path, target: str = "acme_store") -> list[str]:
    return [
        target,
        "--display-name",
        "Acme Store With Spaces",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--repo-root",
        str(repo_root),
    ]


def test_plugin_create_success_uses_sectioned_tui_and_preserves_spaced_values(tmp_path):
    result = _run("scripts/dev/plugin-create.sh", *_plugin_create_args(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "\n"
        "[+] Target scaffold\n"
        "    [v] [acme_store] Created the target source package.\n"
        "    [v] [acme_store] Created the target test package.\n"
        "\n"
        "[+] Next steps\n"
        "    [i] Run ./scripts/dev/setup.sh --acme_store.\n"
        "    [i] Run ./scripts/dev/plugin-check.sh --acme_store.\n"
        "\n"
        "[+] Scaffold result\n"
        "    [v] [acme_store] Target scaffold created.\n"
        "\n"
    )
    plugin = tmp_path / "src/core/scrapers/plugins/acme_store/plugin.py"
    assert "Acme Store With Spaces" in plugin.read_text(encoding="utf-8")


@pytest.mark.parametrize("debug_index", [0, 1, 3, 5, 7, 9])
def test_plugin_create_accepts_debug_between_complete_arguments(tmp_path, debug_index):
    args = _plugin_create_args(tmp_path / str(debug_index), target=f"acme_{debug_index}")
    args.insert(debug_index, "--debug")

    result = _run("scripts/dev/plugin-create.sh", *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", f"[acme_{debug_index}] Target scaffold created.")
    assert f"scaffold\t1\tacme_{debug_index}" in result.stderr


def test_plugin_create_debug_alone_preserves_parser_status_and_exposes_diagnostics():
    result = _run("scripts/dev/plugin-create.sh", "--debug")

    assert result.returncode == 2
    assert "the following arguments are required" in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_plugin_create_help_has_precedence_in_every_position(args):
    result = _run("scripts/dev/plugin-create.sh", *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")


def test_plugin_create_accepts_duplicate_debug_without_forwarding_it(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        "--debug",
        *_plugin_create_args(tmp_path),
        "--debug",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("scaffold\t1\tacme_store") == 1


def test_plugin_create_preserves_duplicate_option_and_invalid_argument_semantics(tmp_path):
    duplicate_option = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(tmp_path),
        "--display-name",
        "Last Store Name",
    )
    invalid = _run("scripts/dev/plugin-create.sh", "--unknown")
    duplicate_target = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(tmp_path / "duplicate"),
        "second_target",
    )

    assert duplicate_option.returncode == 0
    plugin = tmp_path / "src/core/scrapers/plugins/acme_store/plugin.py"
    assert "Last Store Name" in plugin.read_text(encoding="utf-8")
    assert invalid.returncode == 2
    assert duplicate_target.returncode == 2
    assert "unrecognized arguments" not in invalid.stdout
    assert "unrecognized arguments" not in invalid.stderr
    assert_task_status(invalid.stdout, "x", "Target scaffold could not be created.")
    assert_task_status(duplicate_target.stdout, "x", "Target scaffold could not be created.")


def _noisy_python(tmp_path: Path, scaffold_status: int | None = None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python3"
    scaffold_failure = ""
    if scaffold_status is not None:
        scaffold_failure = (
            'case "$*" in\n'
            '  *"core.scrapers.tooling.scaffold"*)\n'
            '    printf "%s\\n" "injected scaffold noise" >&2\n'
            f"    exit {scaffold_status}\n"
            "    ;;\n"
            "esac\n"
        )
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "injected python noise" >&2\n'
        f"{scaffold_failure}"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def test_plugin_create_normal_hides_subprocess_noise(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(tmp_path / "output"),
        env=_noisy_python(tmp_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "injected python noise" not in result.stdout
    assert "injected python noise" not in result.stderr


def test_plugin_create_normal_failure_hides_raw_noise_and_preserves_status(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(tmp_path / "output"),
        env=_noisy_python(tmp_path, scaffold_status=23),
    )

    assert result.returncode == 23
    assert "injected python noise" not in result.stdout
    assert "injected python noise" not in result.stderr
    assert "injected scaffold noise" not in result.stdout
    assert "injected scaffold noise" not in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert_task_status(
        result.stdout,
        "i",
        "Run ./scripts/dev/plugin-create.sh --debug to inspect the failure.",
    )


def test_plugin_create_debug_exposes_noise_and_preserves_command_failure(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        "--debug",
        *_plugin_create_args(tmp_path / "output"),
        env=_noisy_python(tmp_path, scaffold_status=23),
    )

    assert result.returncode == 23
    assert "injected python noise" in result.stderr
    assert "injected scaffold noise" in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert "injected scaffold noise" not in result.stdout
    assert_task_status(
        result.stdout,
        "i",
        "Review the underlying diagnostic above, then retry.",
    )


@pytest.mark.parametrize("script", HELP_SCRIPTS)
def test_shell_help_is_useful_and_framed_by_blank_lines(script):
    result = _run(script, "--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert len([line for line in result.stdout.splitlines() if line]) >= 2


def test_plugin_check_help_needs_no_venv():
    result = _run("scripts/dev/plugin-check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: ./scripts/dev/plugin-check.sh [-h] --<target>" in result.stdout
    assert "target plugin to verify (for example, --" in result.stdout


def test_dev_setup_help_has_no_install_side_effect():
    result = _run("scripts/dev/setup.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "without systemd" in result.stdout
    expected_targets = {plugin.target for plugin in PluginCatalog.discover().plugins}
    listed_targets = {
        line.strip().split(maxsplit=1)[0].removeprefix("--")
        for line in result.stdout.splitlines()
        if line.startswith("  --") and not line.startswith("  --debug")
    }
    assert listed_targets == expected_targets


def test_check_help_needs_no_venv_and_describes_full_gate():
    result = _run("scripts/dev/check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "With no argument, run the complete local pre-push gate." in result.stdout


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

    assert 'pip_install -r "$REQUIREMENTS_FILE"' in install
    assert 'pip_install -r "$req_path"' in install
    assert '-m pip install -q --upgrade "$@"' in install
    assert '-m pip install --upgrade "$@"' in install
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
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
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
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
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
    assert_task_status(result.stdout, "x", "Cannot enable hooks; .githooks/pre-push is missing.")
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
    assert_task_status(result.stdout, "x", ".githooks/pre-push has invalid POSIX shell syntax.")
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


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "--debug"),
    ),
)
def test_hook_installer_accepts_debug_and_duplicate_debug(tmp_path, args):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["sh", str(installer), *args],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", "Repository-local pre-push checks are enabled.")
    assert "true" in result.stderr
    assert ".githooks" in result.stderr


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_hook_installer_help_has_precedence_in_every_position(tmp_path, args):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(
        ["sh", str(installer), *args],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert "--debug" in result.stdout


@pytest.mark.parametrize("argument", ("bad", "--", "--unknown"))
def test_hook_installer_rejects_invalid_arguments_with_framed_ui(tmp_path, argument):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(
        ["sh", str(installer), argument],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+]")
    assert result.stdout.endswith("\n\n")
    assert_task_status(result.stdout, "x", f"Invalid argument: {argument}")


def _noisy_git(tmp_path: Path):
    fake_bin = tmp_path / "noisy-bin"
    fake_bin.mkdir()
    git = fake_bin / "git"
    real_git = shutil.which("git")
    assert real_git is not None
    git.write_text(
        f"""#!/bin/sh
printf '%s\\n' 'injected git stderr' >&2
case " $* " in
    *" config "*)
        case " $* " in
            *" --get "*) ;;
            *)
                printf '%s\\n' 'injected git stdout'
                [ "${{NOISY_GIT_FAIL_CONFIG:-0}}" != "1" ] || exit 23 ;;
        esac ;;
esac
exec {real_git} "$@"
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    return fake_bin


def test_hook_installer_debug_exposes_noise_hidden_by_normal_mode(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    fake_bin = _noisy_git(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    normal = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(installer), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 0
    assert "injected git" not in normal.stdout + normal.stderr
    assert "injected git stdout" in debug.stdout + debug.stderr
    assert "injected git stderr" in debug.stdout + debug.stderr


def test_hook_installer_debug_preserves_noisy_command_failure_status(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    fake_bin = _noisy_git(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["NOISY_GIT_FAIL_CONFIG"] = "1"

    normal = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(installer), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 23
    assert "injected git" not in normal.stdout + normal.stderr
    assert "injected git stdout" in debug.stdout + debug.stderr
    assert "injected git stderr" in debug.stdout + debug.stderr
    assert_task_status(normal.stdout, "x", "Could not configure the repository-local hooks path.")
    assert_task_status(debug.stdout, "x", "Could not configure the repository-local hooks path.")


def test_hook_installer_no_worktree_is_a_framed_no_op(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    shutil.rmtree(project / ".git")
    result = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("\n[+]")
    assert result.stdout.endswith("\n\n")
    assert_task_status(result.stdout, "i", "Git hooks were not configured (no Git worktree found).")


@pytest.mark.parametrize("columns", ("40", "80", "100", "160"))
def test_hook_task_semantics_are_width_independent_and_rendering_is_responsive(tmp_path, columns):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    argument = "invalid-" + "-".join(("long",) * 18)
    env = os.environ.copy()
    env["COLUMNS"] = columns

    result = subprocess.run(
        ["sh", str(installer), argument],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", f"Invalid argument: {argument}")
    continuation_present = any(line.startswith("        ") for line in result.stdout.splitlines())
    assert continuation_present is (int(columns) < 160)


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
    shutil.copy(ROOT / "tests/support.py", project / "tests/support.py")

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
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Setup arguments\n")
    assert_task_status(result.stdout, "x", "Invalid argument: target")
    assert result.stdout.endswith("\n\n")


def test_dev_setup_rejects_multiple_targets_before_installing():
    result = _run("scripts/dev/setup.sh", "--skroutz", "--insomnia")
    assert result.returncode == 1
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "Select at most one target.")


def test_dev_setup_rejects_unknown_target_before_pip_work():
    result = _run("scripts/dev/setup.sh", "--does_not_exist")
    assert result.returncode == 1
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "Unknown target 'does_not_exist'.")
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
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "The development venv path is a symlink.")
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
    output = result.stdout + result.stderr
    if script == "scripts/dev/setup.sh":
        assert "System Python 3.10 or newer is required." in output
    else:
        assert "Detected Python 3.9.18" in output
    assert "3.10 or newer" in output


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
    assert "[+] Git hook setup" not in result.stdout
    assert_task_status(result.stdout, "v", "Repository-local pre-push checks are enabled.")


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
    assert result.stderr == ""
    assert "existing development venv uses an unsupported Python" in result.stdout


def _setup_project(tmp_path: Path):
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

    fake_bin = tmp_path / "setup-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
if [ "${SETUP_NOISE:-0}" = "1" ]; then
    printf '%s\\n' 'injected python stdout'
    printf '%s\\n' 'injected python stderr' >&2
fi
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) printf '%s\\n' '3.12.0' ;; esac
        exit 0 ;;
    -m)
        case "${2:-}" in
            core.scrapers.tooling.cli)
                printf 'alpha\\t%s\\nbeta\\t\\n' "$ALPHA_REQ"
                exit 0 ;;
            venv)
                mkdir -p "$3/bin"
                cp "$0" "$3/bin/python3"
                chmod 755 "$3/bin/python3"
                exit 0 ;;
            pip)
                stage=packaging
                case " $* " in
                    *" check "*) stage=check ;;
                    *" requirements.txt "*) stage=requirements ;;
                    *" $ALPHA_REQ "*) stage=target ;;
                esac
                [ "${SETUP_FAIL_STAGE:-}" != "$stage" ] || exit 23
                exit 0 ;;
        esac ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    alpha = tmp_path / "alpha-private.req"
    alpha.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ALPHA_REQ": str(alpha),
        }
    )
    return project, env


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "--debug"),
        ("--alpha", "--debug"),
        ("--debug", "--alpha"),
    ),
)
def test_dev_setup_accepts_debug_in_supported_positions(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("\n[+] Environment checks\n")
    assert result.stdout.endswith("\n\n")
    assert "[+] Setup complete" in result.stdout
    if "--debug" in args:
        assert "alpha\t" not in result.stdout
        assert "alpha\t" in result.stderr


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_dev_setup_help_has_precedence_in_every_position(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert "--debug" in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--",),
        ("bad",),
        ("--alpha", "--alpha"),
        ("--debug", "--alpha", "--beta"),
    ),
)
def test_dev_setup_invalid_and_duplicate_flags_keep_exit_one(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Setup arguments\n")
    assert result.stdout.endswith("\n\n")


def test_dev_setup_normal_hides_noise_and_debug_exposes_it(tmp_path):
    project, env = _setup_project(tmp_path)
    env["SETUP_NOISE"] = "1"

    normal = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 0
    assert "injected python" not in normal.stdout + normal.stderr
    assert "injected python stdout" in debug.stdout + debug.stderr
    assert "injected python stderr" in debug.stdout + debug.stderr


def test_dev_setup_debug_preserves_noisy_command_failure_status(tmp_path):
    project, env = _setup_project(tmp_path)
    env.update({"SETUP_NOISE": "1", "SETUP_FAIL_STAGE": "target"})

    normal = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 23
    assert "injected python" not in normal.stdout + normal.stderr
    assert "injected python stdout" in debug.stdout + debug.stderr
    assert "injected python stderr" in debug.stdout + debug.stderr
    message = "    [x] Private dependencies for the alpha target could not be installed."
    assert message in normal.stdout
    assert message in debug.stdout
    assert normal.stdout.startswith("\n[+] Environment checks\n")
    assert normal.stdout.endswith("\n\n")


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
