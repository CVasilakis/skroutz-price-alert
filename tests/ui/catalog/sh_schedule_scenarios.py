"""SH_SCHEDULE scenarios: every user-facing transcript schedule.sh can produce.

schedule.sh re-applies configured cadences to the installed timers, so the worlds
vary the interval-resolution status, the resolved-vs-installed [Timer] blocks, and
the catalog health.
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import (
    WORLD_ALL_ORPHANS,
    WORLD_AMAZON_UNINSTALLED,
    WORLD_BROKEN_CATALOG,
    WORLD_EMPTY,
    WORLD_INSTALLED,
    WORLD_NO_VENV,
    WORLD_ORPHAN,
    ShellWorld,
    shell_case,
)

_case = shell_case(Surface.SH_SCHEDULE, "scripts/schedule.sh")

#: The catalog resolves a 2h cadence while the installed timer still runs hourly.
_CHANGED = replace(WORLD_INSTALLED, schedules={"skroutz": "*-*-* 00/2:00:00"})

_case(
    "help",
    "Usage text with the supported-interval vocabulary and per-installed flags.",
    "--help",
    world=WORLD_INSTALLED,
    tags=("help",),
)

_case(
    "help_no_venv",
    "Help still renders without a venv - intervals shown as unavailable.",
    "--help",
    world=WORLD_NO_VENV,
    tags=("help", "catalog"),
)

_case(
    "invalid_argument",
    "A positional argument is rejected.",
    "foo",
    world=WORLD_INSTALLED,
    tags=("error",),
)

_case(
    "catalog_unavailable_venv_missing",
    "Units exist but the venv is gone - repair hint.",
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
    "orphan_skipped_no_flag",
    "No flags: the orphan is reported and skipped, the rest proceed.",
    world=WORLD_ORPHAN,
    tags=("orphan",),
)

_case(
    "all_orphans",
    "Every installed unit is an orphan - nothing to schedule.",
    world=WORLD_ALL_ORPHANS,
    tags=("orphan",),
)

_case("nothing_installed", "No installed scrapers at all.", world=WORLD_EMPTY)

_case(
    "no_config",
    "The scraper's config file is missing - timer left unchanged.",
    world=replace(WORLD_INSTALLED, interval_status={"skroutz": "nocfg"}),
)

_case(
    "invalid_interval",
    "The config sets an unsupported interval - timer left unchanged.",
    world=replace(WORLD_INSTALLED, interval_status={"skroutz": "invalid"}),
)

_case(
    "malformed_config",
    "A structurally malformed config leaves its timer unchanged and exits 15.",
    world=replace(
        WORLD_INSTALLED,
        schedule_errors={"skroutz": "Remove unsupported keys from `config/skroutz.json`."},
    ),
    tags=("error", "target_config"),
)

_case(
    "missing_schedule",
    "A scheduled target has no catalog-resolved schedule - skipped.",
    world=replace(WORLD_INSTALLED, schedules={}),
    tags=("error",),
)

_case(
    "already_matches",
    "The installed timer already matches the configured interval.",
    world=WORLD_INSTALLED,
)

_case("updated", "The cadence changed - the timer unit is rewritten and re-armed.", world=_CHANGED)

_case(
    "partial_config_failure",
    "A healthy cadence is applied while a malformed target remains unchanged.",
    world=ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        schedules={"skroutz": "*-*-* 00/2:00:00", "amazon": "hourly"},
        schedule_errors={"amazon": "Remove unsupported keys from `config/amazon.json`."},
    ),
    tags=("error", "target_config"),
)

_case(
    "debug_updated",
    "Debug mode exposes catalog, schedule, and systemd command output.",
    "--debug",
    world=replace(
        _CHANGED,
        systemctl_stdout="injected systemctl stdout",
        systemctl_stderr="injected systemctl stderr",
    ),
)

_case(
    "restart_fails",
    "An active changed timer cannot be re-armed; the desired file is retained.",
    world=replace(
        _CHANGED,
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        systemctl_fail=("restart",),
    ),
    tags=("error",),
)

_case(
    "debug_restart_fails",
    "Debug mode preserves failure status while exposing the failing command output.",
    "--debug",
    world=replace(
        _CHANGED,
        enabled_timers=("skroutz",),
        active_timers=("skroutz",),
        systemctl_fail=("restart",),
        systemctl_stdout="injected failing systemctl stdout",
        systemctl_stderr="injected failing systemctl stderr",
    ),
    tags=("error",),
)

_case(
    "daemon_reload_fails",
    "The desired file remains on disk when daemon-reload fails.",
    world=replace(_CHANGED, systemctl_fail=("daemon-reload",)),
    tags=("error",),
)

# Atomic timer replacement must fail cleanly when its staging file cannot be created.
_case(
    "write_fails",
    "The systemd user dir is unwritable while applying a new cadence.",
    world=replace(_CHANGED, installed_services=(), unit_dir_readonly=True),
    tags=("error",),
)
