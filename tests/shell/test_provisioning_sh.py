import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

SYSTEMCTL = r"""#!/bin/sh
set -eu
[ "${1:-}" = "--user" ] && shift
runtime=0
[ "${1:-}" = "--runtime" ] && { runtime=1; shift; }
verb="$1"
shift
[ "${FAKE_SIGNAL_VERB:-}" = "$verb" ] && kill -TERM "$PPID"
stem() {
    value="${1##*/}"
    value="${value%-scraper.timer}"
    printf '%s' "${value%-scraper.service}"
}
marker() {
    printf '%s/%s.%s' "$FAKE_STATE" "$1" "$(stem "$2")"
}
case "$verb" in
    show)
        property="$2"
        unit="$3"
        target="$(stem "$unit")"
        case "$property" in
            LoadState)
                { [ -e "$XDG_CONFIG_HOME/systemd/user/$unit" ] ||
                    [ -L "$XDG_CONFIG_HOME/systemd/user/$unit" ]; } &&
                    echo LoadState=loaded || echo LoadState=not-found ;;
            UnitFileState)
                if [ -f "$(marker enabled "$unit")" ]; then
                    echo UnitFileState=enabled
                elif [ -f "$(marker enabled_runtime "$unit")" ]; then
                    echo UnitFileState=enabled-runtime
                elif [ "${FAKE_UNIT_FILE_STATE_TARGET:-}" = "$target" ]; then
                    printf 'UnitFileState=%s\n' "${FAKE_UNIT_FILE_STATE:-disabled}"
                else
                    echo UnitFileState=disabled
                fi ;;
            ActiveState)
                if [ "${FAKE_UNEXPECTED_TARGET:-}" = "$target" ]; then
                    echo ActiveState=activating
                elif [ -f "$(marker active "$unit")" ]; then
                    echo ActiveState=active
                else
                    echo ActiveState=inactive
                fi ;;
        esac ;;
    daemon-reload)
        [ "${FAKE_FAIL_DAEMON:-0}" != "1" ] ;;
    enable)
        now=0
        [ "${1:-}" = "--now" ] && { now=1; shift; }
        unit="$1"
        if [ "$now" -eq 1 ] &&
           [ "${FAKE_FAIL_ENABLE_TARGET:-}" = "$(stem "$unit")" ]; then
            exit 1
        fi
        if [ "$runtime" -eq 1 ]; then
            : > "$(marker enabled_runtime "$unit")"
        else
            : > "$(marker enabled "$unit")"
        fi
        [ "$now" -eq 0 ] || : > "$(marker active "$unit")" ;;
    start)
        : > "$(marker active "$1")" ;;
    stop)
        rm -f "$(marker active "$1")" ;;
    disable)
        unit="$1"
        if [ "$runtime" -eq 1 ]; then
            rm -f "$(marker enabled_runtime "$unit")"
        else
            rm -f "$(marker enabled "$unit")"
        fi
        if [ -L "$XDG_CONFIG_HOME/systemd/user/$unit" ]; then
            rm -f "$XDG_CONFIG_HOME/systemd/user/$unit"
        fi ;;
    reset-failed) [ "${FAKE_FAIL_RESET_FAILED:-0}" != "1" ;;
esac
"""


@pytest.fixture
def shell_world(tmp_path):
    base = tmp_path / "project with spaces"
    unit_dir = tmp_path / "xdg" / "systemd" / "user"
    state = tmp_path / "state"
    fake_bin = tmp_path / "bin"
    base.mkdir()
    unit_dir.mkdir(parents=True)
    state.mkdir()
    fake_bin.mkdir()
    systemctl = fake_bin / "systemctl"
    systemctl.write_text(SYSTEMCTL, encoding="utf-8")
    systemctl.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
            "FAKE_STATE": str(state),
            "NO_COLOR": "1",
        }
    )
    return base, unit_dir, state, env


def run_transaction(shell_world, targets, schedules, mode="normal", **env_updates):
    base, _, _, env = shell_world
    env.update({key: str(value) for key, value in env_updates.items()})
    script = f"""
set -eu
BASE_DIR={shlex_quote(str(base))}
. {shlex_quote(str(ROOT / "scripts/lib/common.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/systemd.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/provisioning.sh"))}
targets={shlex_quote(targets)}
schedules={shlex_quote(schedules)}
provision_units_transaction "$targets" "$schedules" {shlex_quote(mode)}
"""
    return subprocess.run(["sh", "-c", script], text=True, capture_output=True, env=env)


def run_schedule_transaction(shell_world, targets, schedules, **env_updates):
    base, _, _, env = shell_world
    env.update({key: str(value) for key, value in env_updates.items()})
    script = f"""
set -eu
BASE_DIR={shlex_quote(str(base))}
. {shlex_quote(str(ROOT / "scripts/lib/common.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/systemd.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/provisioning.sh"))}
targets={shlex_quote(targets)}
schedules={shlex_quote(schedules)}
schedule_units_transaction "$targets" "$schedules"
"""
    return subprocess.run(["sh", "-c", script], text=True, capture_output=True, env=env)


def run_disable_one(shell_world, target, **env_updates):
    base, unit_dir, state, env = shell_world
    env.update({key: str(value) for key, value in env_updates.items()})
    for suffix in ("timer", "service"):
        (unit_dir / f"{target}-scraper.{suffix}").touch()
    (state / f"enabled.{target}").touch()
    (state / f"active.{target}").touch()
    script = f"""
set -eu
BASE_DIR={shlex_quote(str(base))}
. {shlex_quote(str(ROOT / "scripts/lib/common.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/systemd.sh"))}
disable_one {shlex_quote(target)}
"""
    return subprocess.run(["sh", "-c", script], text=True, capture_output=True, env=env)


def test_disable_does_not_reset_a_healthy_unit(shell_world):
    result = run_disable_one(
        shell_world,
        "alpha",
        FAKE_FAIL_RESET_FAILED="1",
    )

    assert result.returncode == 0, result.stderr


def shlex_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def install_fake_command(shell_world, name: str, body: str):
    _, _, _, env = shell_world
    fake_bin = Path(env["PATH"].split(":", 1)[0])
    command = fake_bin / name
    command.write_text(body, encoding="utf-8")
    command.chmod(0o755)


def test_successful_first_install_writes_and_activates_pair(shell_world):
    _, unit_dir, state, _ = shell_world
    result = run_transaction(shell_world, "alpha", "alpha\thourly")
    assert result.returncode == 0, result.stderr
    assert (unit_dir / "alpha-scraper.service").is_file()
    assert (unit_dir / "alpha-scraper.timer").is_file()
    assert (state / "enabled.alpha").is_file()
    assert (state / "active.alpha").is_file()


def test_first_install_activation_failure_removes_every_new_unit(shell_world):
    _, unit_dir, _, _ = shell_world
    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_FAIL_ENABLE_TARGET="alpha",
    )
    assert result.returncode != 0
    assert list(unit_dir.glob("*-scraper.*")) == []
    assert "restoring previous files and states" in result.stderr


def test_reinstall_failure_restores_bytes_and_timer_state(shell_world):
    _, unit_dir, state, _ = shell_world
    service = unit_dir / "alpha-scraper.service"
    timer = unit_dir / "alpha-scraper.timer"
    service.write_bytes(b"old service bytes\n")
    timer.write_bytes(b"old timer bytes\n")
    (state / "enabled.alpha").touch()
    (state / "active.alpha").touch()

    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_FAIL_ENABLE_TARGET="alpha",
    )
    assert result.returncode != 0
    assert service.read_bytes() == b"old service bytes\n"
    assert timer.read_bytes() == b"old timer bytes\n"
    assert (state / "enabled.alpha").is_file()
    assert (state / "active.alpha").is_file()


def test_reinstall_failure_restores_runtime_enabled_state(shell_world):
    _, unit_dir, state, _ = shell_world
    (unit_dir / "alpha-scraper.service").write_text("old service\n", encoding="utf-8")
    (unit_dir / "alpha-scraper.timer").write_text("old timer\n", encoding="utf-8")
    (state / "enabled_runtime.alpha").touch()

    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_FAIL_ENABLE_TARGET="alpha",
    )

    assert result.returncode != 0
    assert (state / "enabled_runtime.alpha").is_file()
    assert not (state / "enabled.alpha").exists()


@pytest.mark.parametrize("link_kind", ("relative", "absolute", "devnull"))
def test_reinstall_rejects_symlinked_units_before_mutation(shell_world, link_kind):
    _, unit_dir, state, _ = shell_world
    external = unit_dir.parent / "external"
    external.mkdir()
    expected_targets = {}
    for suffix in ("service", "timer"):
        live = unit_dir / f"alpha-scraper.{suffix}"
        target = external / f"original.{suffix}"
        target.write_text(f"linked {suffix}\n", encoding="utf-8")
        if link_kind == "relative":
            link_target = os.path.relpath(target, unit_dir)
        elif link_kind == "absolute":
            link_target = str(target)
        else:
            link_target = "/dev/null"
        live.symlink_to(link_target)
        expected_targets[live] = link_target
    (state / "active.alpha").touch()

    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_UNIT_FILE_STATE_TARGET="alpha",
        FAKE_UNIT_FILE_STATE="linked",
    )

    assert result.returncode != 0
    assert "Refusing to replace managed unit symlink" in result.stderr
    for live, target in expected_targets.items():
        assert live.is_symlink()
        assert os.readlink(live) == target
    assert not (state / "enabled.alpha").exists()
    assert (state / "active.alpha").is_file()


def test_multi_target_failure_rolls_back_target_already_activated(shell_world):
    _, unit_dir, state, _ = shell_world
    result = run_transaction(
        shell_world,
        "alpha\nbeta",
        "alpha\thourly\nbeta\tdaily",
        FAKE_FAIL_ENABLE_TARGET="beta",
    )
    assert result.returncode != 0
    assert list(unit_dir.glob("*-scraper.*")) == []
    assert list(state.iterdir()) == []


def test_backup_copy_failure_never_removes_existing_units(shell_world):
    _, unit_dir, _, _ = shell_world
    expected = {}
    for target in ("alpha", "beta"):
        for suffix in ("service", "timer"):
            path = unit_dir / f"{target}-scraper.{suffix}"
            payload = f"old {target} {suffix}\n".encode()
            path.write_bytes(payload)
            expected[path] = payload
    install_fake_command(
        shell_world,
        "cp",
        """#!/bin/sh
case "$*" in
    *alpha-scraper.service*backups*) exit 1 ;;
esac
exec /bin/cp "$@"
""",
    )
    result = run_transaction(
        shell_world,
        "alpha\nbeta",
        "alpha\thourly\nbeta\tdaily",
    )
    assert result.returncode != 0
    assert "before any live file was changed" in result.stderr
    for path, payload in expected.items():
        assert path.read_bytes() == payload


def test_failure_during_individual_move_rolls_back_all_units(shell_world):
    _, unit_dir, _, _ = shell_world
    expected = {}
    for target in ("alpha", "beta"):
        for suffix in ("service", "timer"):
            path = unit_dir / f"{target}-scraper.{suffix}"
            payload = f"old {target} {suffix}\n".encode()
            path.write_bytes(payload)
            expected[path] = payload
    install_fake_command(
        shell_world,
        "mv",
        """#!/bin/sh
case "$1" in
    *staged/alpha-scraper.timer) exit 1 ;;
esac
exec /bin/mv "$@"
""",
    )
    result = run_transaction(
        shell_world,
        "alpha\nbeta",
        "alpha\thourly\nbeta\tdaily",
    )
    assert result.returncode != 0
    for path, payload in expected.items():
        assert path.read_bytes() == payload


def test_signal_during_move_rolls_back_and_exits_with_signal_status(shell_world):
    _, unit_dir, _, _ = shell_world
    service = unit_dir / "alpha-scraper.service"
    timer = unit_dir / "alpha-scraper.timer"
    service.write_bytes(b"old service\n")
    timer.write_bytes(b"old timer\n")
    install_fake_command(
        shell_world,
        "mv",
        """#!/bin/sh
/bin/mv "$@"
case "$1" in
    *staged/alpha-scraper.service) kill -TERM "$PPID" ;;
esac
""",
    )
    result = run_transaction(shell_world, "alpha", "alpha\thourly")
    assert result.returncode == 143
    assert "Unit replacement interrupted by TERM" in result.stderr
    assert service.read_bytes() == b"old service\n"
    assert timer.read_bytes() == b"old timer\n"


def test_signal_during_activation_rolls_back_new_units(shell_world):
    _, unit_dir, _, _ = shell_world
    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_SIGNAL_VERB="enable",
    )
    assert result.returncode == 143
    assert "Unit replacement interrupted by TERM" in result.stderr
    assert list(unit_dir.glob("*-scraper.*")) == []


def test_symlink_rejection_prevents_activation_signal(shell_world):
    _, unit_dir, state, _ = shell_world
    external = unit_dir.parent / "external"
    external.mkdir()
    expected_targets = {}
    for suffix in ("service", "timer"):
        live = unit_dir / f"alpha-scraper.{suffix}"
        target = external / f"original.{suffix}"
        target.write_text(f"linked {suffix}\n", encoding="utf-8")
        link_target = os.path.relpath(target, unit_dir)
        live.symlink_to(link_target)
        expected_targets[live] = link_target
    (state / "active.alpha").touch()

    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_SIGNAL_VERB="enable",
        FAKE_UNIT_FILE_STATE_TARGET="alpha",
        FAKE_UNIT_FILE_STATE="linked",
    )

    assert result.returncode != 0
    assert "Refusing to replace managed unit symlink" in result.stderr
    for live, target in expected_targets.items():
        assert live.is_symlink()
        assert os.readlink(live) == target
    assert not (state / "enabled.alpha").exists()
    assert (state / "active.alpha").is_file()


def test_signal_during_success_cleanup_cannot_start_rollback(shell_world):
    _, unit_dir, state, _ = shell_world
    install_fake_command(
        shell_world,
        "rm",
        """#!/bin/sh
case "$*" in
    *.scrooge-units.*) kill -TERM "$PPID" ;;
esac
exec /bin/rm "$@"
""",
    )
    result = run_transaction(shell_world, "alpha", "alpha\thourly")
    assert result.returncode == 0, result.stderr
    assert (unit_dir / "alpha-scraper.service").is_file()
    assert (unit_dir / "alpha-scraper.timer").is_file()
    assert (state / "enabled.alpha").is_file()
    assert (state / "active.alpha").is_file()


def test_rollback_failure_retains_private_recovery_artifacts(shell_world):
    _, unit_dir, _, _ = shell_world
    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_FAIL_DAEMON="1",
    )
    assert result.returncode != 0
    recovery = list(unit_dir.glob(".scrooge-units.*"))
    assert len(recovery) == 1
    assert "Recovery files were retained at" in result.stderr


def test_unexpected_timer_state_refuses_before_live_files_change(shell_world):
    _, unit_dir, _, _ = shell_world
    (unit_dir / "alpha-scraper.timer").write_text("old timer\n", encoding="utf-8")
    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        FAKE_UNEXPECTED_TARGET="alpha",
    )
    assert result.returncode != 0
    assert (unit_dir / "alpha-scraper.timer").read_text(encoding="utf-8") == "old timer\n"


@pytest.mark.parametrize("unit_file_state", ("masked", "static", "indirect", "linked"))
def test_disabled_like_unit_file_states_are_accepted(shell_world, unit_file_state):
    _, unit_dir, _, _ = shell_world
    (unit_dir / "alpha-scraper.service").write_text("old service\n", encoding="utf-8")
    (unit_dir / "alpha-scraper.timer").write_text("old timer\n", encoding="utf-8")
    result = run_transaction(
        shell_world,
        "alpha",
        "alpha\thourly",
        mode="deferred",
        FAKE_UNIT_FILE_STATE_TARGET="alpha",
        FAKE_UNIT_FILE_STATE=unit_file_state,
    )
    assert result.returncode == 0, result.stderr


def test_schedule_transaction_rejects_symlink_without_rewriting_service(shell_world):
    _, unit_dir, _, _ = shell_world
    service = unit_dir / "alpha-scraper.service"
    service.write_text("preserve service bytes\n", encoding="utf-8")
    external = unit_dir.parent / "external.timer"
    external.write_text("OnCalendar=hourly\n", encoding="utf-8")
    timer = unit_dir / "alpha-scraper.timer"
    timer.symlink_to(os.path.relpath(external, unit_dir))

    result = run_schedule_transaction(
        shell_world,
        "alpha",
        "alpha\tdaily",
        FAKE_UNIT_FILE_STATE_TARGET="alpha",
        FAKE_UNIT_FILE_STATE="linked",
    )

    assert result.returncode != 0
    assert timer.is_symlink()
    assert "Refusing to replace managed unit symlink" in result.stderr
    assert service.read_text(encoding="utf-8") == "preserve service bytes\n"
    assert external.read_text(encoding="utf-8") == "OnCalendar=hourly\n"


def test_schedule_rejects_dangling_symlink_text(shell_world):
    _, unit_dir, _, _ = shell_world
    (unit_dir / "alpha-scraper.service").write_text("service\n", encoding="utf-8")
    timer = unit_dir / "alpha-scraper.timer"
    timer.symlink_to("../missing/original.timer")

    result = run_schedule_transaction(
        shell_world,
        "alpha",
        "alpha\tdaily",
        FAKE_FAIL_DAEMON="1",
    )

    assert result.returncode != 0
    assert timer.is_symlink()
    assert os.readlink(timer) == "../missing/original.timer"
    assert "Refusing to replace managed unit symlink" in result.stderr


def test_systemd_analyze_accepts_rendered_pair_from_path_with_spaces(tmp_path):
    systemd_analyze = shutil.which("systemd-analyze")
    if systemd_analyze is None:
        if os.environ.get("CI"):
            pytest.fail("systemd-analyze is required in CI")
        pytest.skip("systemd-analyze is unavailable")
    base = tmp_path / "checkout with spaces"
    scripts = base / "scripts"
    units = tmp_path / "units"
    scripts.mkdir(parents=True)
    units.mkdir()
    run_sh = scripts / "run.sh"
    run_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    run_sh.chmod(0o755)
    service = units / "alpha-scraper.service"
    timer = units / "alpha-scraper.timer"
    script = f"""
set -eu
BASE_DIR={shlex_quote(str(base))}
. {shlex_quote(str(ROOT / "scripts/lib/common.sh"))}
. {shlex_quote(str(ROOT / "scripts/lib/systemd.sh"))}
render_plugin_service alpha {shlex_quote(str(service))}
render_plugin_timer alpha hourly {shlex_quote(str(timer))}
"""
    rendered = subprocess.run(["sh", "-c", script], text=True, capture_output=True)
    assert rendered.returncode == 0, rendered.stderr
    verified = subprocess.run(
        [systemd_analyze, "verify", str(service), str(timer)],
        text=True,
        capture_output=True,
    )
    assert verified.returncode == 0, verified.stderr
