"""SH_DISABLE scenarios: every user-facing transcript disable.sh can produce.

Teardown semantics differ from enable/schedule: a registered-but-not-installed
target is a yellow *notice* (exit 0), not an error, and orphans are first-class
disable targets (glob-derived, no registry needed).
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_AMAZON_UNINSTALLED,
    WORLD_EMPTY,
    WORLD_HEALTHY,
    WORLD_INSTALLED,
    WORLD_ORPHAN,
    shell_case,
)

_case = shell_case(Surface.SH_DISABLE, "scripts/disable.sh")

_case("help", "Usage text listing registered targets and installed orphans alike.",
      "--help", world=WORLD_ORPHAN, tags=("help", "orphan"))

_case("invalid_argument", "A positional argument is rejected.",
      "foo", world=WORLD_INSTALLED, tags=("error",))

_case("bare_double_dash", "A bare '--' is rejected instead of silently selecting nothing.",
      "--", world=WORLD_INSTALLED, tags=("error",))

_case("help_after_target", "-h/--help is honored anywhere in the argument list.",
      "--skroutz", "--help", world=WORLD_INSTALLED, tags=("help",))

_case("not_installed_notice", "A registered but never-installed target - notice, not an error.",
      "--amazon", world=WORLD_AMAZON_UNINSTALLED)

_case("unknown_target", "An explicit --<target> in neither the registry nor the units.",
      "--bogus", world=WORLD_INSTALLED, tags=("error",))

_case("nothing_installed", "No scraper timers on disk at all.",
      world=WORLD_EMPTY)

_case("already_disabled", "The timer and service are already fully stopped.",
      world=WORLD_INSTALLED)

_case("disable_success", "An enabled and active timer is stopped and disabled.",
      world=WORLD_HEALTHY)

_case("orphan_by_name", "An orphan's still-armed timer is disabled by explicit --<target>.",
      "--ghost",
      world=replace(WORLD_ORPHAN, enabled_timers=("ghost",), active_timers=("ghost",)),
      tags=("orphan",))
