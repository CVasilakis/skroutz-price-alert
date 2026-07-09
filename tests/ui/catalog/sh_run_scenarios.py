"""SH_RUN scenarios: every user-facing transcript run.sh can produce.

run.sh is the dispatcher the systemd services call; its own output is flag
validation plus the final exec into the venv python. The sandbox's venv responder
prints an ``[exec] python3 <script> <args>`` marker for that exec, so the goldens
lock which entry point and arguments each flag combination dispatches to.
"""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import WORLD_NO_VENV, ShellWorld, shell_case

_case = shell_case(Surface.SH_RUN, "scripts/run.sh")

_TWO = ShellWorld(plugins=("skroutz", "amazon"))

_case("help", "Usage text with the fixed flags plus one row per registered target.",
      "--help", world=_TWO, tags=("help",))

_case("invalid_positional", "A positional argument is rejected (with the help text).",
      "foo", tags=("error",))

_case("unknown_flag", "An unregistered --<target> flag (registry readable).",
      "--ghost", tags=("error",))

_case("registry_unreadable", "An unknown flag while the registry is unreadable - diagnose only.",
      "--ghost", world=WORLD_NO_VENV, tags=("error", "registry"))

_case("ping_not_alone", "--ping combined with another flag is rejected.",
      "--ping", "--quiet", tags=("error",))

_case("ping_repeated", "A repeated --ping still violates the must-be-used-alone rule.",
      "--ping", "--ping", tags=("error",))

_case("bare_double_dash", "A bare '--' is rejected (it would parse as an empty target name).",
      "--", tags=("error",))

_case("no_venv_dispatch", "No flags with the venv missing: the repair hint, not a raw exec failure.",
      world=WORLD_NO_VENV, tags=("error",))

_case("status_not_alone", "--status combined with another flag is rejected.",
      "--status", "--skroutz", tags=("error",))

_case("dispatch_default", "No flags: dispatches to main.py.")

_case("dispatch_target_quiet", "--quiet --<target>: the systemd ExecStart shape.",
      "--quiet", "--skroutz")

_case("dispatch_ping", "--ping alone dispatches to ping.py.",
      "--ping")

_case("dispatch_status", "--status alone dispatches to status.py.",
      "--status")
