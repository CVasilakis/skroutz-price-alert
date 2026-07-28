"""SH_INSTALL_HOOKS scenarios: direct setup of versioned Git checks."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_INSTALL_HOOKS, "scripts/dev/install-hooks.sh")

_case(
    "help",
    "Hook installer usage and debug documentation.",
    "--help",
    tags=("help",),
)

_case(
    "configured",
    "The hook is validated and the repository-local path is retained.",
    tags=("ok",),
)

_case(
    "no_worktree",
    "A directory without a Git worktree is an informational no-op.",
    world=ShellWorld(git_worktree=False),
    tags=("ok",),
)

_case(
    "missing_hook",
    "A missing versioned hook fails before permissions or Git configuration change.",
    world=ShellWorld(hook_state="missing"),
    tags=("error",),
)

_case(
    "invalid_hook",
    "Invalid hook syntax fails after the executable-bit check.",
    world=ShellWorld(hook_state="invalid"),
    tags=("error",),
)

_case(
    "config_failure",
    "A Git configuration failure retains actionable recovery guidance.",
    world=ShellWorld(git_fail=("config",)),
    tags=("error",),
)
