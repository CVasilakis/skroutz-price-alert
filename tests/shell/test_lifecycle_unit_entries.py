import os
import subprocess
from pathlib import Path

import pytest
import ui.catalog  # noqa: F401
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env


def _run(checkout: Path, script: str, *args: str, world: ShellWorld):
    return subprocess.run(
        ["/bin/sh", str(checkout / script), *args],
        cwd=checkout,
        env=_fake_env(checkout, world),
        text=True,
        capture_output=True,
        timeout=30,
    )


def _replace_pair_with_links(checkout: Path, target: str, kind: str):
    unit_dir = checkout / "xdg/systemd/user"
    external_dir = checkout / "external-units"
    external_dir.mkdir()
    external_paths = []
    for suffix in ("timer", "service"):
        live = unit_dir / f"{target}-scraper.{suffix}"
        live.unlink()
        external = external_dir / f"{target}.{suffix}"
        external.write_text(f"external {suffix}\n", encoding="utf-8")
        external_paths.append(external)
        if kind == "relative":
            destination = os.path.relpath(external, unit_dir)
        elif kind == "absolute":
            destination = str(external)
        elif kind == "dangling":
            destination = f"../missing/{target}.{suffix}"
        else:
            destination = "/dev/null"
        live.symlink_to(destination)
    return external_paths


@pytest.mark.parametrize("kind", ("relative", "absolute", "dangling", "devnull"))
@pytest.mark.parametrize("selected", (False, True))
def test_uninstall_removes_unit_links_without_following_targets(kind, selected):
    world = ShellWorld(
        installed_timers=("alpha",),
        installed_services=("alpha",),
        plugins=("alpha",),
    )
    checkout = _build_sandbox(world)
    try:
        external_paths = _replace_pair_with_links(checkout, "alpha", kind)
        args = ("--alpha",) if selected else ()
        result = _run(checkout, "scripts/uninstall.sh", *args, world=world)
        assert result.returncode == 0, result.stdout + result.stderr
        unit_dir = checkout / "xdg/systemd/user"
        assert not (unit_dir / "alpha-scraper.timer").is_symlink()
        assert not (unit_dir / "alpha-scraper.service").is_symlink()
        for external in external_paths:
            assert external.read_text(encoding="utf-8").startswith("external ")
    finally:
        _cleanup(checkout)


@pytest.mark.parametrize("script", ("install.sh", "scripts/schedule.sh"))
@pytest.mark.parametrize("kind", ("relative", "absolute", "dangling", "devnull"))
def test_write_workflows_reject_unit_links_without_file_or_state_changes(script, kind):
    world = ShellWorld(
        installed_timers=("alpha",),
        installed_services=("alpha",),
        enabled_timers=("alpha",),
        active_timers=("alpha",),
        plugins=("alpha",),
        schedules={"alpha": "daily"},
        config_files=("alpha.json", "general.json"),
    )
    checkout = _build_sandbox(world)
    try:
        _replace_pair_with_links(checkout, "alpha", kind)
        unit_dir = checkout / "xdg/systemd/user"
        before_links = {path.name: os.readlink(path) for path in unit_dir.glob("alpha-scraper.*")}
        state_dir = checkout / "systemd-state"
        before_state = sorted(path.name for path in state_dir.iterdir())
        result = _run(checkout, script, "--alpha", world=world)
        assert result.returncode != 0
        if script == "install.sh":
            assert "A managed systemd unit destination is unsafe." in result.stdout
            assert "Refusing to replace managed unit symlink" not in result.stderr
        else:
            assert "Refusing to replace managed unit symlink" in result.stderr
        assert {
            path.name: os.readlink(path) for path in unit_dir.glob("alpha-scraper.*")
        } == before_links
        assert sorted(path.name for path in state_dir.iterdir()) == before_state
    finally:
        _cleanup(checkout)
