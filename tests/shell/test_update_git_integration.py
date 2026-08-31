import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env


def git(*args, cwd: Path, env=None):
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def real_git_update_world(tmp_path):
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
    )
    checkout = _build_sandbox(world)
    host_git = shutil.which("git")
    assert host_git is not None
    (checkout / "bin" / "git").unlink()
    (checkout / "bin" / "git").symlink_to(host_git)

    (checkout / ".gitignore").write_text(
        "\n".join(("bin/", "config/", "home/", "systemd-state/", "venv/", "xdg/")) + "\n",
        encoding="utf-8",
    )
    git("init", "-q", "-b", "main", cwd=checkout)
    git("config", "user.name", "Scrooge Test", cwd=checkout)
    git("config", "user.email", "scrooge@example.invalid", cwd=checkout)
    git("add", ".", cwd=checkout)
    git("commit", "-q", "-m", "base", cwd=checkout)

    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-q", "--initial-branch=main", str(origin)],
        check=True,
    )
    git("remote", "add", "origin", str(origin), cwd=checkout)
    git("push", "-q", "-u", "origin", "main", cwd=checkout)

    env = _fake_env(checkout, world)
    env.update(
        {
            "GIT_AUTHOR_NAME": "Scrooge Test",
            "GIT_AUTHOR_EMAIL": "scrooge@example.invalid",
            "GIT_COMMITTER_NAME": "Scrooge Test",
            "GIT_COMMITTER_EMAIL": "scrooge@example.invalid",
        }
    )
    yield checkout, origin, env
    _cleanup(checkout)


def push_remote_change(tmp_path: Path, origin: Path):
    remote_work = tmp_path / "remote-work"
    subprocess.run(["git", "clone", "-q", str(origin), str(remote_work)], check=True)
    git("config", "user.name", "Scrooge Test", cwd=remote_work)
    git("config", "user.email", "scrooge@example.invalid", cwd=remote_work)
    (remote_work / "release.txt").write_text("remote release\n", encoding="utf-8")
    git("add", "release.txt", cwd=remote_work)
    git("commit", "-q", "-m", "remote release", cwd=remote_work)
    git("push", "-q", "origin", "main", cwd=remote_work)


def run_update(checkout: Path, env, *args: str):
    return subprocess.run(
        ["/bin/sh", str(checkout / "scripts/update.sh"), *args],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_real_git_fast_forward_updates_checkout(real_git_update_world, tmp_path):
    checkout, origin, env = real_git_update_world
    push_remote_change(tmp_path, origin)
    result = run_update(checkout, env)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (checkout / "release.txt").read_text(encoding="utf-8") == "remote release\n"
    assert git("status", "--porcelain", cwd=checkout).stdout == ""


def test_real_git_fetch_repairs_pruned_origin_main(real_git_update_world):
    checkout, _, env = real_git_update_world
    git("update-ref", "-d", "refs/remotes/origin/main", cwd=checkout)
    result = run_update(checkout, env)
    assert result.returncode == 0, result.stdout + result.stderr
    git("rev-parse", "--verify", "refs/remotes/origin/main", cwd=checkout)


def test_real_git_debug_survives_fast_forward_and_reaches_deferred_install(
    real_git_update_world, tmp_path
):
    checkout, origin, env = real_git_update_world
    push_remote_change(tmp_path, origin)
    report = "general_config\tgeneral\tcurrent\tconfig/general.json\t"
    env.update(
        {
            "FAKE_MIGRATION_REPORT": report,
            "FAKE_MIGRATION_STDERR": "debug migration boundary",
            "FAKE_PIP_STDERR": "debug deferred install boundary",
        }
    )
    result = run_update(checkout, env, "--debug")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "debug migration boundary" in result.stderr
    assert "debug deferred install boundary" in result.stderr
    assert report in result.stderr.splitlines()
    assert report not in result.stdout


def test_real_git_dirty_tree_is_refused_without_prompt(real_git_update_world):
    checkout, _, env = real_git_update_world
    (checkout / "scripts/update.sh").write_text(
        (checkout / "scripts/update.sh").read_text(encoding="utf-8") + "\n# dirty\n",
        encoding="utf-8",
    )
    result = run_update(checkout, env)
    assert result.returncode != 0
    assert "working tree contains" in result.stdout
    assert "proceed" not in (result.stdout + result.stderr).lower()


def test_real_git_ahead_main_is_refused(real_git_update_world):
    checkout, _, env = real_git_update_world
    (checkout / "local.txt").write_text("local\n", encoding="utf-8")
    git("add", "local.txt", cwd=checkout)
    git("commit", "-q", "-m", "local", cwd=checkout)
    result = run_update(checkout, env)
    assert result.returncode != 0
    assert "fetched update failed safety validation" in result.stdout


def test_real_git_diverged_history_is_refused(real_git_update_world, tmp_path):
    checkout, origin, env = real_git_update_world
    (checkout / "local.txt").write_text("local\n", encoding="utf-8")
    git("add", "local.txt", cwd=checkout)
    git("commit", "-q", "-m", "local", cwd=checkout)
    push_remote_change(tmp_path, origin)
    result = run_update(checkout, env)
    assert result.returncode != 0
    assert "fetched update failed safety validation" in result.stdout


def test_real_git_wrong_branch_is_refused_without_switching(real_git_update_world):
    checkout, _, env = real_git_update_world
    git("switch", "-q", "-c", "feature", cwd=checkout)
    result = run_update(checkout, env)
    assert result.returncode != 0
    assert "not on branch 'main'" in result.stdout
    assert git("branch", "--show-current", cwd=checkout).stdout.strip() == "feature"


def test_real_git_missing_origin_is_refused_before_fetch(real_git_update_world):
    checkout, _, env = real_git_update_world
    git("remote", "remove", "origin", cwd=checkout)
    result = run_update(checkout, env)
    assert result.returncode == 1
    assert "remote 'origin' is missing or unusable" in result.stdout
    assert "Fetched origin/main" not in result.stdout


def test_activation_failure_disables_every_selected_target():
    world = ShellWorld(
        plugins=("alpha", "beta"),
        installed_timers=("alpha", "beta"),
        installed_services=("alpha", "beta"),
        enabled_timers=("alpha", "beta"),
        active_timers=("alpha", "beta"),
        systemctl_fail=("start",),
        systemctl_fail_target="beta",
        config_files=("alpha.json", "beta.json", "general.json"),
    )
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world))
        assert result.returncode != 0
        assert "All selected targets were left disabled for safety." in result.stdout
        state = checkout / "systemd-state"
        for target in ("alpha", "beta"):
            assert not (state / f"enabled.{target}").exists()
            assert not (state / f"timer_active.{target}").exists()
    finally:
        _cleanup(checkout)


def test_update_machine_migration_hides_child_noise_and_parses_only_tsv():
    report = ("target_config\tskroutz\tfailed\tconfig/skroutz.json\tinvalid legacy config",)
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
        migration_report=report,
        migration_stderr="injected migration noise",
        migration_status=15,
    )
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world))
        assert result.returncode == 15
        assert "injected migration noise" not in result.stdout + result.stderr
        assert report[0] not in result.stdout + result.stderr
        assert "[config/skroutz.json] Migration failed: invalid legacy config" in result.stdout
    finally:
        _cleanup(checkout)


def test_update_aborts_when_nonzero_migration_has_no_failed_outcome():
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
        migration_status=19,
    )
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world))
        assert result.returncode == 19
        assert "JSON migration infrastructure failed." in result.stdout
        assert "Managed JSON documents are ready" not in result.stdout
        state = checkout / "systemd-state"
        assert not (state / "enabled.skroutz").exists()
        assert not (state / "timer_active.skroutz").exists()
    finally:
        _cleanup(checkout)


def test_update_internal_debug_mirrors_migration_tsv_to_stderr_without_corrupting_it():
    report = "general_config\tgeneral\tcurrent\tconfig/general.json\t"
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
        migration_report=(report,),
        migration_stderr="injected migration noise",
    )
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world), "--debug")
        assert result.returncode == 0, result.stdout + result.stderr
        assert report in result.stderr.splitlines()
        assert "injected migration noise" in result.stderr.splitlines()
        assert report not in result.stdout
    finally:
        _cleanup(checkout)


@pytest.mark.parametrize("args", [("--debug", "--help"), ("--help", "--debug")])
def test_update_debug_is_compatible_with_help_in_either_position(args):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world), *args)
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout.startswith("\nUsage:")
        assert result.stdout.endswith("\n\n")
        assert "--debug" in result.stdout
    finally:
        _cleanup(checkout)


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (("--debug", "--debug"), "--debug flag may be specified only once"),
        (("--help", "--help"), "accepts no arguments other than"),
        (("--debug", "invalid"), "Invalid argument: invalid"),
        (("--debug", "--help", "extra"), "accepts no arguments other than"),
    ],
)
def test_update_invalid_and_duplicate_flags_keep_exit_one(args, message):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        result = run_update(checkout, _fake_env(checkout, world), *args)
        assert result.returncode == 1
        assert message in result.stdout
        assert result.stdout.startswith("\n")
        assert result.stdout.endswith("\n\n")
    finally:
        _cleanup(checkout)


def test_update_normal_hides_noise_and_debug_streams_it_without_changing_failure_status():
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
        git_stdout="injected git stdout",
        git_stderr="injected git stderr",
        pip_fail="upgrade",
        pip_stdout="injected pip stdout",
        pip_stderr="injected pip stderr",
        systemctl_stdout="injected systemctl stdout",
        systemctl_stderr="injected systemctl stderr",
    )
    checkouts = [_build_sandbox(world), _build_sandbox(world)]
    try:
        normal = run_update(checkouts[0], _fake_env(checkouts[0], world))
        debug = run_update(checkouts[1], _fake_env(checkouts[1], world), "--debug")
        assert normal.returncode == debug.returncode == 1
        for noise in (
            "injected git stdout",
            "injected git stderr",
            "injected pip stdout",
            "injected pip stderr",
            "injected systemctl stdout",
            "injected systemctl stderr",
        ):
            assert noise not in normal.stdout + normal.stderr
            assert noise in debug.stdout + debug.stderr
        assert "Provisioning failed after the source update." in normal.stdout
        assert "Provisioning failed after the source update." in debug.stdout
    finally:
        for checkout in checkouts:
            _cleanup(checkout)


def test_update_success_owns_exact_outer_padding_and_section_spacing():
    world = ShellWorld(
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        config_files=("skroutz.json", "general.json"),
    )
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        result = run_update(checkout, env)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.startswith("\n[+]")
        assert result.stdout.endswith("\n\n")
        assert "\n\n\n" not in result.stdout
    finally:
        _cleanup(checkout)


def test_signal_during_update_success_cleanup_cannot_disable_restored_target():
    world = ShellWorld(
        installed_timers=("alpha",),
        installed_services=("alpha",),
        enabled_timers=("alpha",),
        active_timers=("alpha",),
        config_files=("alpha.json", "general.json"),
        plugins=("alpha",),
    )
    checkout = _build_sandbox(world)
    fake_rm = checkout / "bin/rm"
    fake_rm.unlink()
    fake_rm.write_text(
        """#!/bin/sh
case "$*" in
    *.scrooge-update.*) kill -TERM "$PPID" ;;
esac
exec /bin/rm "$@"
""",
        encoding="utf-8",
    )
    fake_rm.chmod(0o755)
    try:
        result = run_update(checkout, _fake_env(checkout, world))
        assert result.returncode == 0, result.stdout + result.stderr
        state = checkout / "systemd-state"
        assert (state / "enabled.alpha").exists()
        assert (state / "timer_active.alpha").exists()
        assert list((checkout / "xdg/systemd/user").glob(".scrooge-update.*")) == []
    finally:
        _cleanup(checkout)


def test_signal_before_any_target_is_stopped_removes_the_recovery_workspace():
    """The private workspace is owned from the moment its path is known.

    The interrupt lands after ``create_private_workspace`` returned but before
    any target was quiesced - the window that only the ``workspace`` phase
    covers. Nothing on the system has changed, so the handler must delete the
    workspace outright rather than leave an unreferenced directory in
    SYSTEMD_USER_DIR, which nothing else ever cleans up.
    """
    world = ShellWorld(
        installed_timers=("alpha",),
        installed_services=("alpha",),
        enabled_timers=("alpha",),
        active_timers=("alpha",),
        config_files=("alpha.json", "general.json"),
        plugins=("alpha",),
    )
    checkout = _build_sandbox(world)
    pid_file = checkout / "update.pid"
    fake_mkdir = checkout / "bin/mkdir"
    fake_mkdir.unlink()
    fake_mkdir.write_text(
        """#!/bin/sh
/bin/mkdir "$@" || exit $?
case "$*" in
    *.scrooge-update.*backups*) kill -TERM "$(cat "$UPDATE_PID_FILE")" ;;
esac
""",
        encoding="utf-8",
    )
    fake_mkdir.chmod(0o755)
    env = _fake_env(checkout, world)
    env["UPDATE_PID_FILE"] = str(pid_file)
    try:
        # `exec` keeps the recorded PID: the shim signals the update shell
        # itself, never a helper subshell that would swallow it.
        result = subprocess.run(
            ["/bin/sh", "-c", f'echo $$ > "{pid_file}"; exec /bin/sh scripts/update.sh'],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert result.returncode == 143, result.stdout + result.stderr
        assert "Update interrupted by TERM." in result.stdout
        unit_dir = checkout / "xdg/systemd/user"
        assert list(unit_dir.glob(".scrooge-update.*")) == []
        state = checkout / "systemd-state"
        assert (state / "enabled.alpha").exists()
        assert (state / "timer_active.alpha").exists()
    finally:
        _cleanup(checkout)


_PREFLIGHT_WORLD = ShellWorld(
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
    config_files=("skroutz.json", "general.json"),
)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"git_worktree": False}, "is not a Git worktree."),
        (
            {"git_dirty": True},
            "Error: The working tree contains tracked changes or nonignored untracked files.",
        ),
        ({"git_branch": "beta"}, "requires branch 'main' (current branch: 'beta')."),
        ({"git_origin": False}, "Error: Git remote 'origin' is missing or unusable."),
        ({"git_relation": "ahead"}, "Error: Local main has commits that are not contained"),
        (
            {"fetched_paths_valid": False},
            "is missing required file 'scrooge-alert'.",
        ),
    ],
)
def test_debug_surfaces_each_preflight_git_refusal(overrides, message):
    """Only --debug explains *why* a preflight check refused the update.

    A normal run shows the panel verdict alone, because update.sh invokes every
    check through run_update_helper, which discards both streams. These
    messages therefore live on the debug path only, and nothing else executes
    them - so they are asserted here rather than left to drift.
    """
    world = replace(_PREFLIGHT_WORLD, **overrides)
    for args, expected in ((("--debug",), True), ((), False)):
        checkout = _build_sandbox(world)
        try:
            result = run_update(checkout, _fake_env(checkout, world), *args)
            transcript = (result.stdout + result.stderr).replace(str(checkout), "<BASE_DIR>")
            assert result.returncode == 1, transcript
            assert (message in transcript) is expected, transcript
        finally:
            _cleanup(checkout)
