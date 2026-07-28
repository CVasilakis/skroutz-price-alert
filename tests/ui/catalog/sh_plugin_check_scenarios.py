"""SH_PLUGIN_CHECK scenarios: isolated verification of one target."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_PLUGIN_CHECK, "scripts/dev/plugin-check.sh")

_case(
    "help",
    "Target verification usage documents target selection and debug output.",
    "--help",
    tags=("help",),
)

_case(
    "verified",
    "The target contract, tests, and all static checks pass.",
    "--skroutz",
    tags=("ok",),
)

_case(
    "source_failure",
    "A source or dependency-contract failure stops verification before tests.",
    "--skroutz",
    world=ShellWorld(plugin_check_fail="source"),
    tags=("error",),
)

_case(
    "tests_failure",
    "A target-test failure preserves the earlier contract success.",
    "--skroutz",
    world=ShellWorld(plugin_check_fail="tests"),
    tags=("error",),
)

_case(
    "type_failure",
    "A type-check failure stops the remaining static checks.",
    "--skroutz",
    world=ShellWorld(plugin_check_fail="type"),
    tags=("error",),
)

_case(
    "lint_failure",
    "A Ruff lint failure preserves the successful type-check result.",
    "--skroutz",
    world=ShellWorld(plugin_check_fail="lint"),
    tags=("error",),
)

_case(
    "format_failure",
    "A Ruff formatting failure preserves every earlier successful task.",
    "--skroutz",
    world=ShellWorld(plugin_check_fail="format"),
    tags=("error",),
)

_case(
    "debug_failure",
    "Debug streams both tool output channels and preserves the tool's status.",
    "--debug",
    "--skroutz",
    world=ShellWorld(
        plugin_check_fail="tests",
        plugin_check_stdout="injected verification stdout",
        plugin_check_stderr="injected verification stderr",
    ),
    tags=("error",),
)
