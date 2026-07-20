"""SH_STOP scenarios: every user-facing transcript stop.sh can produce.

stop.sh mirrors disable.sh's teardown semantics but acts on the *service* units
(aborting an in-flight scrape) rather than the timers.
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_AMAZON_UNINSTALLED,
    WORLD_EMPTY,
    WORLD_INSTALLED,
    WORLD_ORPHAN,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_STOP, "scripts/stop.sh")

_case(
    "help",
    "Usage text with one flag row per stoppable target.",
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
    "not_installed_notice",
    "A registered but never-installed target - notice, not an error.",
    "--amazon",
    world=WORLD_AMAZON_UNINSTALLED,
)

_case(
    "unknown_target",
    "An explicit --<target> in neither the registry nor the units.",
    "--bogus",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case("nothing_installed", "No scraper services on disk at all.", world=WORLD_EMPTY)

_case("not_running", "The service exists but nothing is executing.", world=WORLD_INSTALLED)

_case(
    "stop_active",
    "An in-flight scrape (ActiveState=activating) is aborted.",
    world=replace(WORLD_INSTALLED, activating_services=("skroutz",)),
)

_case(
    "stop_fails",
    "systemctl rejects a stop request; success is not reported.",
    world=replace(WORLD_INSTALLED, activating_services=("skroutz",), systemctl_fail=("stop",)),
    tags=("error",),
)

_case(
    "stop_noop_detected",
    "A successful stop that leaves the service running fails verification.",
    world=replace(WORLD_INSTALLED, activating_services=("skroutz",), systemctl_noop=("stop",)),
    tags=("error",),
)

_case(
    "query_fails",
    "A failed service-state query is not mistaken for inactivity.",
    world=replace(WORLD_INSTALLED, activating_services=("skroutz",), systemctl_fail=("show",)),
    tags=("error",),
)

_case(
    "batch_continues_after_failure",
    "A later independent target is still checked after failure.",
    world=ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        activating_services=("skroutz",),
        systemctl_fail=("stop",),
    ),
    tags=("error",),
)

_case(
    "orphan_by_name",
    "An orphan's in-flight service is stopped by explicit --<target>.",
    "--ghost",
    world=replace(WORLD_ORPHAN, activating_services=("ghost",)),
    tags=("orphan",),
)
