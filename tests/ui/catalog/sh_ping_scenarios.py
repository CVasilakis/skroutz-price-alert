"""SH_PING scenarios for the dedicated notification-test wrapper."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import WORLD_NO_VENV, shell_case

_case = shell_case(Surface.SH_PING, "scripts/ping.sh")

_case("help", "Fixed command help is available without Python work.", "--help", tags=("help",))
_case(
    "invalid_argument",
    "Unsupported ping options are rejected with a command-specific help hint.",
    "--quiet",
    tags=("error",),
)
_case(
    "no_venv_dispatch",
    "A missing runtime environment produces the shared repair guidance.",
    world=WORLD_NO_VENV,
    tags=("error", "system"),
)
_case("dispatch", "No flags dispatches directly to ping.py.")
