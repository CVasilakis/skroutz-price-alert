"""SH_UNINSTALL scenarios: every user-facing transcript uninstall.sh can produce."""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_AMAZON_UNINSTALLED,
    WORLD_EMPTY,
    WORLD_INSTALLED,
    WORLD_ORPHAN,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_UNINSTALL, "scripts/uninstall.sh")

_case(
    "help",
    "Usage text with debug documentation and one flag row per registered target.",
    "--help",
    world=WORLD_INSTALLED,
    tags=("help",),
)

_case(
    "help_with_orphans",
    "Help gains a 'Leftover scrapers' section for orphaned units.",
    "--help",
    world=WORLD_ORPHAN,
    tags=("help", "orphan"),
)

_case(
    "help_after_target",
    "-h/--help is honored anywhere in the argument list.",
    "--skroutz",
    "--help",
    world=WORLD_INSTALLED,
    tags=("help",),
)

_case(
    "invalid_argument",
    "A positional argument is rejected.",
    "foo",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "bare_double_dash",
    "A bare '--' is rejected instead of silently selecting nothing.",
    "--",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "unknown_target",
    "An explicit --<target> in neither the catalog nor the units.",
    "--bogus",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "selected_removal",
    "Only the named target's units are removed; the venv stays.",
    "--skroutz",
    world=WORLD_INSTALLED,
)

_case(
    "selected_not_installed",
    "A registered target with no units is reported as a no-op.",
    "--amazon",
    world=WORLD_AMAZON_UNINSTALLED,
)

_case(
    "nothing_installed",
    "No target units are present; full uninstall still removes the project venv.",
    world=WORLD_EMPTY,
)

_case(
    "orphan_removal",
    "An orphan's leftover units can be purged by name.",
    "--ghost",
    world=WORLD_ORPHAN,
    tags=("orphan",),
)

_case(
    "selected_removal_debug",
    "Debug exposes the underlying systemd protocol and command output.",
    "--debug",
    "--skroutz",
    world=replace(
        WORLD_INSTALLED,
        systemctl_stdout="injected systemctl stdout",
        systemctl_stderr="injected systemctl stderr",
    ),
)

_case("full_teardown", "No flags: every unit removed and the venv deleted.", world=WORLD_INSTALLED)

_case(
    "full_teardown_no_venv",
    "Full teardown when the venv is already gone.",
    world=replace(WORLD_INSTALLED, venv=False),
)

_case(
    "partial_failure",
    "One target fails to disable, so no selected unit entries are removed.",
    world=ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        enabled_timers=("skroutz", "amazon"),
        active_timers=("skroutz", "amazon"),
        systemctl_fail=("disable",),
        systemctl_fail_target="amazon",
    ),
    tags=("error",),
)

_case(
    "teardown_fails_safely",
    "A running service that cannot stop prevents all removal.",
    world=replace(WORLD_INSTALLED, activating_services=("skroutz",), systemctl_fail=("stop",)),
    tags=("error",),
)

_case(
    "daemon_reload_fails",
    "Removed unit entries are reported even when the required manager reload fails.",
    "--skroutz",
    world=replace(WORLD_INSTALLED, systemctl_fail=("daemon-reload",)),
    tags=("error",),
)
