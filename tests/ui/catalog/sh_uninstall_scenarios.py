"""SH_UNINSTALL scenarios: every user-facing transcript uninstall.sh can produce."""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_EMPTY,
    WORLD_INSTALLED,
    WORLD_ORPHAN,
    shell_case,
)

_case = shell_case(Surface.SH_UNINSTALL, "scripts/uninstall.sh")

_case("help", "Usage text with one flag row per registered target.",
      "--help", world=WORLD_INSTALLED, tags=("help",))

_case("help_with_orphans", "Help gains a 'Leftover scrapers' section for orphaned units.",
      "--help", world=WORLD_ORPHAN, tags=("help", "orphan"))

_case("invalid_argument", "A positional argument is rejected.",
      "foo", world=WORLD_INSTALLED, tags=("error",))

_case("unknown_target", "An explicit --<target> in neither the registry nor the units.",
      "--bogus", world=WORLD_INSTALLED, tags=("error",))

_case("selected_removal", "Only the named target's units are removed; the venv stays.",
      "--skroutz", world=WORLD_INSTALLED)

_case("orphan_removal", "An orphan's leftover units can be purged by name.",
      "--ghost", world=WORLD_ORPHAN, tags=("orphan",))

_case("full_teardown", "No flags: every unit removed and the venv deleted.",
      world=WORLD_INSTALLED)

_case("full_teardown_no_venv", "Full teardown when the venv is already gone.",
      world=replace(WORLD_INSTALLED, venv=False))
