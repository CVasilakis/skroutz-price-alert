import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, drive_shell


@pytest.mark.parametrize(
    ("script", "world", "marker"),
    (
        (
            "scripts/enable.sh",
            ShellWorld(installed_timers=("skroutz",), installed_services=("skroutz",)),
            "Background schedule enabled and started",
        ),
        (
            "scripts/disable.sh",
            ShellWorld(
                installed_timers=("skroutz",),
                installed_services=("skroutz",),
                enabled_timers=("skroutz",),
                active_timers=("skroutz",),
            ),
            "Stopping and disabling background execution",
        ),
        (
            "scripts/stop.sh",
            ShellWorld(installed_services=("skroutz",), active_services=("skroutz",)),
            "Stopping active background execution",
        ),
        (
            "scripts/schedule.sh",
            ShellWorld(
                installed_timers=("skroutz",),
                installed_services=("skroutz",),
                active_timers=("skroutz",),
                schedules={"skroutz": "daily"},
            ),
            "Updating the timer schedule",
        ),
        (
            "scripts/uninstall.sh",
            ShellWorld(installed_timers=("skroutz",), installed_services=("skroutz",)),
            "Removed 'skroutz' scraper units",
        ),
    ),
)
def test_duplicate_target_flags_perform_one_action(script, world, marker):
    result = drive_shell(script, "--skroutz", "--skroutz", world=world)
    assert result.exit_code == 0
    assert result.renderable.plain.count(marker) == 1
