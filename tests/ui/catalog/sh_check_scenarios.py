"""SH_CHECK scenarios: the repository-wide non-mutating acceptance gate."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_CHECK, "scripts/dev/check.sh")

_case(
    "help",
    "Acceptance-gate usage documents modes and underlying debug output.",
    "--help",
    tags=("help",),
)

_case(
    "full_pass",
    "Every static, shell, dependency, and test task passes.",
    tags=("ok",),
)

_case(
    "tests_only",
    "A selected test-only run omits unrelated gate sections.",
    "tests",
    tags=("ok",),
)

_case(
    "lint_failure",
    "An immediate Ruff lint failure stops the gate without later tasks.",
    "static",
    world=ShellWorld(check_fail="lint"),
    tags=("error",),
)

_case(
    "format_failure",
    "A Ruff formatting failure preserves the earlier lint success.",
    "static",
    world=ShellWorld(check_fail="format"),
    tags=("error",),
)

_case(
    "dependency_failure",
    "A dependency inconsistency preserves completed static and shell phases.",
    world=ShellWorld(check_fail="dependencies"),
    tags=("error",),
)

_case(
    "tests_failure",
    "A test failure occurs after all earlier full-gate phases pass.",
    world=ShellWorld(check_fail="tests"),
    tags=("error",),
)

_case(
    "debug_failure",
    "Debug exposes both captured test streams and preserves pytest's status.",
    "--debug",
    "tests",
    world=ShellWorld(
        check_fail="tests",
        check_stdout="injected check stdout",
        check_stderr="injected check stderr",
    ),
    tags=("error",),
)
