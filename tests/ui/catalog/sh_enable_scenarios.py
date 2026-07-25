"""SH_ENABLE scenarios: every user-facing transcript enable.sh can produce."""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_ALL_ORPHANS,
    WORLD_AMAZON_UNINSTALLED,
    WORLD_BROKEN_CATALOG,
    WORLD_EMPTY,
    WORLD_HEALTHY,
    WORLD_INSTALLED,
    WORLD_NO_VENV,
    WORLD_ORPHAN,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_ENABLE, "scripts/enable.sh")

_case(
    "help",
    "Usage text with one flag row per installed (non-orphan) target.",
    "--help",
    world=WORLD_INSTALLED,
    tags=("help",),
)

_case(
    "invalid_argument",
    "A positional argument is rejected.",
    "foo",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "catalog_unavailable",
    "Units exist but the venv is gone - refuse with a repair hint.",
    world=WORLD_NO_VENV,
    tags=("error", "catalog"),
)

_case(
    "catalog_unavailable_discovery_failed",
    "Units exist but plugin discovery raises.",
    world=WORLD_BROKEN_CATALOG,
    tags=("error", "catalog"),
)

_case(
    "selected_orphan",
    "An explicit --<target> whose plugin was removed upstream.",
    "--ghost",
    world=WORLD_ORPHAN,
    tags=("error", "orphan"),
)

_case(
    "selected_not_installed",
    "An explicit --<target> that is registered but never installed.",
    "--amazon",
    world=WORLD_AMAZON_UNINSTALLED,
    tags=("error",),
)

_case(
    "unknown_target",
    "An explicit --<target> in neither the catalog nor the units.",
    "--bogus",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "unknown_target_nothing_installed",
    "An unknown --<target> with no units installed (no hint line).",
    "--bogus",
    world=WORLD_EMPTY,
    tags=("error",),
)

_case(
    "all_orphans",
    "Every installed unit is an orphan - nothing to enable.",
    world=WORLD_ALL_ORPHANS,
    tags=("orphan",),
)

_case("nothing_installed", "No installed scrapers at all.", world=WORLD_EMPTY)

_case("already_enabled", "The timer is already enabled and active.", world=WORLD_HEALTHY)

_case("enable_success", "An installed but dormant timer is armed.", world=WORLD_INSTALLED)

_case(
    "enable_healthy_with_other_bad_config",
    "A malformed config for another target does not block enabling this timer.",
    "--skroutz",
    world=ShellWorld(
        plugins=("skroutz", "insomnia"),
        schedule_errors={"insomnia": "Remove unsupported keys from `config/insomnia.json`."},
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
    ),
)

_case(
    "enable_fails",
    "systemctl enable --now fails.",
    world=replace(WORLD_INSTALLED, systemctl_fail=("enable",)),
    tags=("error",),
)

_case(
    "enable_noop_detected",
    "A zero exit without an enabled/active timer fails verification.",
    world=replace(WORLD_INSTALLED, systemctl_noop=("enable",)),
    tags=("error",),
)

_case(
    "query_fails",
    "A failed timer-state query is not treated as a dormant timer.",
    world=replace(WORLD_INSTALLED, systemctl_fail=("show",)),
    tags=("error",),
)
