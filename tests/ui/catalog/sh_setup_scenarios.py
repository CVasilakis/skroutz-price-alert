"""SH_SETUP scenarios: development-environment preparation transcripts."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_SETUP, "scripts/dev/setup.sh")

_case(
    "help",
    "Development setup usage, target selection, and debug documentation.",
    "--help",
    tags=("help",),
)

_case(
    "all_dependencies",
    "The existing venv, shared dependencies, private target requirements, and hook succeed.",
    world=ShellWorld(requirements={"skroutz": "requirements.txt"}),
    tags=("ok",),
)

_case(
    "no_private_dependencies",
    "A target with no private requirements is an informational dependency no-op.",
    "--skroutz",
    tags=("ok", "skipped"),
)

_case(
    "shared_dependency_failure",
    "Shared dependency installation fails before any private target requirements.",
    world=ShellWorld(
        requirements={"skroutz": "requirements.txt"},
        pip_fail="requirements",
    ),
    tags=("error",),
)

_case(
    "target_dependency_failure",
    "A private target dependency fails after the shared environment was updated.",
    world=ShellWorld(
        requirements={"skroutz": "requirements.txt"},
        pip_fail="plugin",
    ),
    tags=("error",),
)

_case(
    "hook_failure",
    "Hook setup failure is reported as setup's own final sub-task with recovery guidance.",
    world=ShellWorld(git_fail=("config",)),
    tags=("error",),
)
