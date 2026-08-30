"""SH_STATUS scenarios for the target-selecting installation-health wrapper.

status.sh validates its own ``--<target>`` flags and then execs the venv Python; the
sandbox's venv responder prints an ``[exec] python3 <script> <args>`` marker for that
exec, so the goldens lock which arguments each invocation forwards. Its known-target
set is wider than run.sh's: an installed-but-unregistered (orphaned) unit is selectable
too, because status is the command that reports it.
"""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_NO_VENV,
    WORLD_ORPHAN,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_STATUS, "scripts/status.sh")

_TWO = ShellWorld(plugins=("skroutz", "amazon"))

_case(
    "help",
    "Usage text with the fixed flags plus one row per registered target.",
    "--help",
    world=_TWO,
    tags=("help",),
)

_case(
    "help_orphan",
    "An orphaned installed target earns its own help row alongside the registered ones.",
    "--help",
    world=WORLD_ORPHAN,
    tags=("help",),
)

_case(
    "help_no_venv",
    "Missing-venv help retains fixed options and explains how to obtain target rows.",
    "--help",
    world=WORLD_NO_VENV,
    tags=("help",),
)

_case(
    "invalid_argument",
    "A positional argument is rejected with a command-specific help hint.",
    "foo",
    tags=("error",),
)

_case(
    "unknown_flag",
    "An unregistered and uninstalled --<target> flag.",
    "--ghost",
    tags=("error",),
)

_case(
    "invalid_target_syntax",
    "A target flag that is not snake_case is rejected.",
    "--Skroutz",
    tags=("error",),
)

_case(
    "no_venv_dispatch",
    "A missing runtime environment produces the shared repair guidance.",
    world=WORLD_NO_VENV,
    tags=("error", "system"),
)

_case("dispatch", "No flags dispatches directly to status.py.")

_case(
    "dispatch_target",
    "A registered target flag is forwarded to status.py.",
    "--skroutz",
    world=_TWO,
)

_case(
    "dispatch_orphan",
    "An orphaned target is selectable so its panel can be inspected alone.",
    "--ghost",
    world=WORLD_ORPHAN,
)
