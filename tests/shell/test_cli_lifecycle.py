from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[2]


def _write_executable(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    checkout = tmp_path / "checkout"
    for relative in ("scripts/lib", "scripts", "completions"):
        (checkout / relative).mkdir(parents=True, exist_ok=True)
    for relative in (
        "scripts/lib/cli.sh",
        "scripts/scrooge-alert",
        "completions/scrooge-alert.bash",
        "completions/scrooge-alert.fish",
    ):
        shutil.copy2(REPO_ROOT / relative, checkout / relative)

    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    tool_dir = tmp_path / "bin"
    tool_dir.mkdir()
    bash = shutil.which("bash")
    assert bash is not None
    (tool_dir / "bash").symlink_to(bash)

    bash_data = tmp_path / "bash-data"
    loader = bash_data / "bash-completion/bash_completion"
    loader.parent.mkdir(parents=True)
    loader.write_text(
        '__load_completion() {\n    . "$XDG_DATA_HOME/bash-completion/completions/$1"\n}\n',
        encoding="utf-8",
    )
    _write_executable(
        tool_dir / "pkg-config",
        "#!/bin/sh\n"
        "case $1 in\n"
        '  --exists) [ "${FAKE_BASH_SUPPORTED:-0}" = 1 ] ;;\n'
        "  --variable=datadir) printf '%s\\n' \"$FAKE_BASH_DATA\" ;;\n"
        "  *) exit 1 ;;\n"
        "esac\n",
    )
    _write_executable(
        tool_dir / "fish",
        "#!/bin/sh\n"
        '[ "${FAKE_FISH_SUPPORTED:-0}" = 1 ] || exit 1\n'
        "last=\n"
        'for argument in "$@"; do last=$argument; done\n'
        'case " $* " in *" -n "*) exit 0 ;; esac\n'
        '[ "$last" = "$FAKE_FISH_COMPLETION_DIR" ]\n',
    )
    env = {
        **os.environ,
        "PATH": f"{tool_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "FAKE_BASH_DATA": str(bash_data),
        "FAKE_BASH_SUPPORTED": "0",
        "FAKE_FISH_SUPPORTED": "0",
        "FAKE_FISH_COMPLETION_DIR": str(home / ".local/share/fish/vendor_completions.d"),
    }
    env.pop("XDG_DATA_HOME", None)
    return checkout, env


def _run_cli(checkout: Path, env: dict[str, str], body: str) -> subprocess.CompletedProcess[str]:
    script = f"""
set -eu
BASE_DIR={checkout!s}
. "$BASE_DIR/scripts/lib/cli.sh"
{body}
"""
    return subprocess.run(
        ["/bin/sh", "-c", script],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _owned(path: Path, checkout: Path, body: str = "owned\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# scrooge-alert checkout: {checkout}\n{body}", encoding="utf-8")


def test_relative_xdg_silently_installs_only_the_launcher(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    env.update(
        XDG_DATA_HOME="relative-data",
        FAKE_BASH_SUPPORTED="1",
        FAKE_FISH_SUPPORTED="1",
    )

    result = _run_cli(
        checkout,
        env,
        "cli_install_artifacts\n"
        'printf \'%s|%s|%s|%s\\n\' "$CLI_BASH_ELIGIBLE" "$CLI_FISH_ELIGIBLE" '
        '"$CLI_BASH_NOTICE" "$CLI_FISH_NOTICE"',
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "0|0||\n"
    assert (Path(env["HOME"]) / ".local/bin/scrooge-alert").is_file()
    assert not (checkout / "relative-data").exists()


def test_clean_capability_probes_install_both_completions_without_profiles(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    sentinel = tmp_path / "profile-ran"
    (Path(env["HOME"]) / ".bashrc").write_text(f"touch {sentinel}\n", encoding="utf-8")
    env.update(FAKE_BASH_SUPPORTED="1", FAKE_FISH_SUPPORTED="1")

    result = _run_cli(
        checkout,
        env,
        'cli_install_artifacts\nprintf \'%s|%s\\n\' "$CLI_BASH_ELIGIBLE" "$CLI_FISH_ELIGIBLE"',
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1|1\n"
    assert not sentinel.exists()
    data_home = Path(env["HOME"]) / ".local/share"
    assert (data_home / "bash-completion/completions/scrooge-alert").is_file()
    assert (data_home / "fish/vendor_completions.d/scrooge-alert.fish").is_file()


@pytest.mark.parametrize("kind", ("regular", "symlink", "directory"))
def test_foreign_completion_is_preserved_with_a_notice(tmp_path: Path, kind: str):
    checkout, env = _checkout(tmp_path)
    env["FAKE_BASH_SUPPORTED"] = "1"
    completion = Path(env["HOME"]) / ".local/share/bash-completion/completions/scrooge-alert"
    completion.parent.mkdir(parents=True)
    if kind == "regular":
        completion.write_text("foreign\n", encoding="utf-8")
    elif kind == "symlink":
        target = tmp_path / "foreign"
        target.write_text("keep\n", encoding="utf-8")
        completion.symlink_to(target)
    else:
        completion.mkdir()

    result = _run_cli(
        checkout,
        env,
        "cli_install_artifacts\nprintf '%s\\n' \"$CLI_BASH_NOTICE\"",
    )

    assert result.returncode == 0, result.stderr
    assert "preserved and skipped" in result.stdout
    if kind == "regular":
        assert completion.read_text(encoding="utf-8") == "foreign\n"
    elif kind == "symlink":
        assert completion.is_symlink()
    else:
        assert completion.is_dir()


def test_lost_capability_removes_only_current_checkout_completions(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    env.update(FAKE_BASH_SUPPORTED="1", FAKE_FISH_SUPPORTED="1")
    installed = _run_cli(checkout, env, "cli_install_artifacts")
    assert installed.returncode == 0, installed.stderr

    env.update(FAKE_BASH_SUPPORTED="0", FAKE_FISH_SUPPORTED="0")
    reconciled = _run_cli(checkout, env, "cli_install_artifacts")

    assert reconciled.returncode == 0, reconciled.stderr
    data_home = Path(env["HOME"]) / ".local/share"
    assert not (data_home / "bash-completion/completions/scrooge-alert").exists()
    assert not (data_home / "fish/vendor_completions.d/scrooge-alert.fish").exists()


def test_optional_write_failure_preserves_prior_completion_and_succeeds(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    env["FAKE_BASH_SUPPORTED"] = "1"
    completion = Path(env["HOME"]) / ".local/share/bash-completion/completions/scrooge-alert"
    _owned(completion, checkout, "prior\n")

    result = _run_cli(
        checkout,
        env,
        "mv() {\n"
        '  [ "${3:-}" != "$CLI_BASH_COMPLETION_PATH" ] || return 1\n'
        '  command mv "$@"\n'
        "}\n"
        "cli_install_artifacts\n"
        "printf '%s\\n' \"$CLI_BASH_NOTICE\"",
    )

    assert result.returncode == 0, result.stderr
    assert "prior file was preserved" in result.stdout
    assert completion.read_text(encoding="utf-8").endswith("prior\n")
    assert (Path(env["HOME"]) / ".local/bin/scrooge-alert").is_file()


def test_uninstall_removes_owned_files_and_preserves_foreign_entries(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    launcher = Path(env["HOME"]) / ".local/bin/scrooge-alert"
    bash_completion = Path(env["HOME"]) / ".local/share/bash-completion/completions/scrooge-alert"
    fish_completion = (
        Path(env["HOME"]) / ".local/share/fish/vendor_completions.d/scrooge-alert.fish"
    )
    _owned(launcher, checkout)
    bash_completion.parent.mkdir(parents=True)
    bash_completion.write_text("foreign\n", encoding="utf-8")
    fish_completion.parent.mkdir(parents=True)
    target = tmp_path / "foreign-fish"
    target.write_text("keep\n", encoding="utf-8")
    fish_completion.symlink_to(target)

    result = _run_cli(checkout, env, "cli_remove_artifacts")

    assert result.returncode == 0, result.stderr
    assert not launcher.exists()
    assert bash_completion.read_text(encoding="utf-8") == "foreign\n"
    assert fish_completion.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep\n"


def test_uninstall_keeps_launcher_for_retry_when_completion_unlink_fails(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    launcher = Path(env["HOME"]) / ".local/bin/scrooge-alert"
    bash_completion = Path(env["HOME"]) / ".local/share/bash-completion/completions/scrooge-alert"
    _owned(launcher, checkout)
    _owned(bash_completion, checkout)

    result = _run_cli(
        checkout,
        env,
        "rm() {\n"
        '  [ "${2:-}" != "$CLI_BASH_COMPLETION_PATH" ] || return 1\n'
        '  command rm "$@"\n'
        "}\n"
        "cli_remove_artifacts",
    )

    assert result.returncode == 1
    assert launcher.is_file()
    assert bash_completion.is_file()


def test_clean_uninstall_allows_reinstall_from_another_checkout(tmp_path: Path):
    first, env = _checkout(tmp_path / "first")
    first_install = _run_cli(first, env, "cli_install_artifacts")
    assert first_install.returncode == 0, first_install.stderr
    first_remove = _run_cli(first, env, "cli_remove_artifacts")
    assert first_remove.returncode == 0, first_remove.stderr

    second, _ = _checkout(tmp_path / "second")
    second_install = _run_cli(second, env, "cli_install_artifacts")

    assert second_install.returncode == 0, second_install.stderr
    launcher = Path(env["HOME"]) / ".local/bin/scrooge-alert"
    assert f"# scrooge-alert checkout: {second}" in launcher.read_text(encoding="utf-8")


def test_bash_completion_accepts_a_future_public_subcommand(tmp_path: Path):
    command = tmp_path / "scrooge-alert"
    _write_executable(
        command,
        "#!/bin/sh\n"
        'if [ "$1" = future ] && [ "$2" = --help ]; then\n'
        "  printf '%s\\n' 'Usage: scrooge-alert future [--help]' '' 'Options:' "
        "'  --help       Show help' '  --future     Future option'\n"
        "  exit 0\n"
        "fi\n"
        "exit 1\n",
    )
    script = (
        f'source "{REPO_ROOT / "completions/scrooge-alert.bash"}"; '
        "COMP_WORDS=(scrooge-alert future --); COMP_CWORD=2; "
        '_scrooge_alert_complete; printf "%s\\n" "${COMPREPLY[@]}"'
    )
    result = subprocess.run(
        [shutil.which("bash") or "bash", "--noprofile", "--norc", "-c", script],
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "--future" in result.stdout.splitlines()


@pytest.mark.skipif(shutil.which("fish") is None, reason="Fish is not installed")
def test_real_fish_reports_the_exact_user_vendor_directory(tmp_path: Path):
    checkout, env = _checkout(tmp_path)
    fish = shutil.which("fish")
    assert fish is not None
    fake_fish = tmp_path / "bin/fish"
    fake_fish.unlink()
    fake_fish.symlink_to(fish)

    result = _run_cli(
        checkout,
        env,
        "cli_preflight_install\nprintf '%s\\n' \"$CLI_FISH_ELIGIBLE\"",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "1\n"


@pytest.mark.skipif(shutil.which("fish") is None, reason="Fish is not installed")
def test_fish_completion_accepts_a_future_public_subcommand(tmp_path: Path):
    script = f"""
function commandline
    printf '%s\\n' scrooge-alert future
end
function scrooge-alert
    test "$argv[1]" = future; and test "$argv[2]" = --help; or return 1
    printf '%s\\n' 'Usage: scrooge-alert future [--help]' '' 'Options:' \\
        '  --help       Show help' '  --future     Future option'
end
source {REPO_ROOT / "completions/scrooge-alert.fish"}
__scrooge_alert_command
__scrooge_alert_options
"""
    result = subprocess.run(
        [shutil.which("fish") or "fish", "--no-config", "-c", script],
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "future" in result.stdout.splitlines()
    assert "--future" in result.stdout
