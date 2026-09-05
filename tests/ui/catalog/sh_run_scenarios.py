"""SH_RUN scenarios: every user-facing transcript run.sh can produce.

run.sh is the scraping dispatcher the systemd services call; its own output is target
validation plus the final exec into the venv Python. The sandbox's venv responder
prints an ``[exec] python3 <script> <args>`` marker for that exec, so the goldens
lock which entry point and arguments each flag combination dispatches to.
"""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_BROKEN_CATALOG,
    WORLD_NO_VENV,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_RUN, "scripts/run.sh")

_TWO = ShellWorld(plugins=("skroutz", "amazon"))
_EMPTY = ShellWorld(plugins=())

_case(
    "help",
    "Usage text with the fixed flags plus one row per registered target.",
    "--help",
    world=_TWO,
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
    "invalid_positional",
    "A positional argument is rejected with a concise help hint.",
    "foo",
    tags=("error",),
)

_case(
    "unknown_flag",
    "An unregistered --<target> flag (catalog available).",
    "--ghost",
    tags=("error",),
)

_case(
    "catalog_unavailable",
    "An unknown flag while plugin discovery raises.",
    "--ghost",
    world=WORLD_BROKEN_CATALOG,
    tags=("error", "catalog"),
)

_case(
    "empty_catalog_unknown_flag",
    "An unknown flag with a healthy empty catalog is an argument error.",
    "--ghost",
    world=_EMPTY,
    tags=("error",),
)

_case(
    "invalid_target_syntax",
    "A target flag that is not snake_case is rejected.",
    "--bad-target",
    tags=("error",),
)

_case(
    "quiet_debug_conflict",
    "--quiet and --debug ask for opposite output modes and are rejected together.",
    "--quiet",
    "--debug",
    tags=("error",),
    # run.sh forwards --debug rather than interpreting it, so this transcript is an
    # ordinary operational failure and must stay under the shell layout guard.
    shell_debug=False,
)

_case(
    "bare_double_dash",
    "A bare '--' is rejected (it would parse as an empty target name).",
    "--",
    tags=("error",),
)

_case(
    "no_venv_dispatch",
    "No flags with the venv missing: the repair hint, not a raw exec failure.",
    world=WORLD_NO_VENV,
    tags=("error",),
)

_case(
    "venv_symlink_rejected",
    "A project venv symlink is rejected before dispatch.",
    world=ShellWorld(venv_symlink=True),
    tags=("error",),
)

_case(
    "venv_unusable",
    "An installed but unusable venv interpreter is rejected before dispatch.",
    world=ShellWorld(venv_python_usable=False),
    tags=("error",),
)

_case(
    "python39_rejected",
    "An installed venv older than Python 3.10 is rejected before dispatch.",
    world=ShellWorld(python_version="3.9.18", python_supported=False),
    tags=("error",),
)

_case("dispatch_default", "No flags: dispatches to run.py.")

_case(
    "dispatch_target_quiet",
    "--quiet --<target>: the systemd ExecStart shape.",
    "--quiet",
    "--skroutz",
)

_case(
    "dispatch_target_debug",
    "--debug --<target>: forwarded to run.py, which owns the runtime's output modes.",
    "--debug",
    "--skroutz",
    shell_debug=False,
)

_case(
    "dispatch_healthy_with_other_bad_config",
    "A malformed config for another target does not block selected dispatch.",
    "--skroutz",
    world=ShellWorld(
        plugins=("skroutz", "insomnia"),
        schedule_errors={"insomnia": "Remove unsupported keys from `config/insomnia.json`."},
    ),
)
