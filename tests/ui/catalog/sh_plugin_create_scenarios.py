"""SH_PLUGIN_CREATE scenarios: additive target scaffold creation."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_PLUGIN_CREATE, "scripts/dev/plugin-create.sh")
_ARGS = (
    "acme",
    "--display-name",
    "Acme Store",
    "--domain",
    "store.example",
    "--url-prefix",
    "/products/",
)

_case(
    "help",
    "Scaffold usage documents required inputs and debug output.",
    "--help",
    tags=("help",),
)

_case(
    "created",
    "A source package and matching test package are created.",
    *_ARGS,
    tags=("ok",),
)

_case(
    "validation_failure",
    "Invalid target input is reported without raw parser diagnostics.",
    *_ARGS,
    world=ShellWorld(
        scaffold_stderr=("Target scaffold failed: target must be a non-reserved snake_case name"),
        scaffold_status=1,
    ),
    tags=("error",),
)

_case(
    "collision",
    "An existing destination is preserved and reported as a scaffold failure.",
    *_ARGS,
    world=ShellWorld(
        scaffold_stderr=(
            "Target scaffold failed: refusing to overwrite existing path(s): "
            "<BASE_DIR>/src/core/scrapers/plugins/acme"
        ),
        scaffold_status=1,
    ),
    tags=("error",),
)

_case(
    "invalid_result",
    "A malformed hidden result is rejected instead of being rendered as success.",
    *_ARGS,
    world=ShellWorld(scaffold_output="unexpected output"),
    tags=("error",),
)

_case(
    "python_missing",
    "A missing supported Python produces a handled prerequisite failure.",
    *_ARGS,
    world=ShellWorld(tools="no-python3"),
    tags=("error", "system"),
)

_case(
    "debug_failure",
    "Debug exposes one failing scaffold command's underlying diagnostics.",
    "--debug",
    *_ARGS,
    world=ShellWorld(
        scaffold_stderr="injected scaffold diagnostic",
        scaffold_status=23,
    ),
    tags=("error",),
)
