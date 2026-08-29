"""The scenario abstraction and registry — the single source of truth's backbone.

A :class:`Scenario` pairs a name/surface/description with a zero-argument ``build``
callable that returns a :class:`BuildResult` (a Rich renderable plus its computed border
color). Scenarios self-register via the :func:`scenario` decorator; the catalog package
imports every ``*_scenarios.py`` module to populate the registry and exposes the
aggregated :data:`ALL_SCENARIOS`.

This module is the import leaf of the catalog: it depends only on stdlib, so the driver
and rendering layers can import :class:`BuildResult` from here without a cycle.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Surface(Enum):
    """The UI surface a scenario belongs to (also the snapshot-filename prefix).

    Member order is the display order of the gallery/report sections (Python surfaces
    in workflow order, then the shell scripts in lifecycle order): ``ALL_SCENARIOS``
    in ``catalog/__init__.py`` is sorted by it, so the enum is the single source of
    truth for section order. The *values* are stable snapshot-filename
    prefixes; never rename them (that would rename every snapshot file). The
    human-readable section labels live in :data:`SURFACE_INFO` instead.
    """

    RUN = "run"  # the interactive scraping panel (run_reporter.InteractiveRunReporter)
    E2E_RUN = "e2e-run"  # the same panel, driven end-to-end by the real ScrapingOrchestrator
    CONFIG = "config"  # the shared Configuration Check panel
    STARTUP = "startup"  # the full interactive pre-scrape console transcript (multi-panel)
    STATUS = "status"  # a status panel (service / not-installed / orphan)
    PING = "ping"  # the ping Notification Check Results panel
    # Shell surfaces: the transcript a management script prints to the terminal.
    # The "sh-" prefix groups them in the gallery and keeps "sh-run" clear of RUN.
    SH_INSTALL = "sh-install"  # scripts/install.sh
    SH_SETUP = "sh-setup"  # scripts/dev/setup.sh
    SH_PLUGIN_CREATE = "sh-plugin-create"  # scripts/dev/plugin-create.sh
    SH_PLUGIN_CHECK = "sh-plugin-check"  # scripts/dev/plugin-check.sh
    SH_CHECK = "sh-check"  # scripts/dev/check.sh
    SH_INSTALL_HOOKS = "sh-install-hooks"  # scripts/dev/install-hooks.sh
    SH_RUN = "sh-run"  # scripts/run.sh
    SH_PING = "sh-ping"  # scripts/ping.sh
    SH_STATUS = "sh-status"  # scripts/status.sh
    SH_MIGRATE = "sh-migrate"  # scripts/dev/migrate.sh
    SH_SCHEDULE = "sh-schedule"  # scripts/schedule.sh
    SH_ENABLE = "sh-enable"  # scripts/enable.sh
    SH_DISABLE = "sh-disable"  # scripts/disable.sh
    SH_STOP = "sh-stop"  # scripts/stop.sh
    SH_UPDATE = "sh-update"  # scripts/update.sh
    SH_UNINSTALL = "sh-uninstall"  # scripts/uninstall.sh


#: Surfaces whose scenario inputs can also be exercised as a quiet/background run.
BACKGROUND_SURFACES = frozenset({Surface.RUN, Surface.E2E_RUN, Surface.STARTUP})


@dataclass(frozen=True)
class SurfaceInfo:
    """The human-readable presentation of one surface (gallery/report only).

    Attributes:
        label (str): The section header shown in the HTML report and the terminal
            gallery (e.g. ``"install.sh"`` instead of the raw ``"sh-install"``).
        blurb (str): A one-line subtitle explaining what the section shows.
    """

    label: str
    blurb: str


#: Presentation label + blurb per surface, consumed by the HTML report and the
#: terminal gallery. Exhaustive over ``Surface`` — guarded by
#: ``tests/ui/test_ui_catalog.py`` so a new surface cannot ship without a label.
SURFACE_INFO: dict[Surface, SurfaceInfo] = {
    Surface.RUN: SurfaceInfo(
        "Scraping panel (interactive)",
        "The live panel a manual ./scrooge-alert run draws while checking items.",
    ),
    Surface.E2E_RUN: SurfaceInfo(
        "Scraping panel (end-to-end)",
        "The same panel painted by the real application workflow against a fake store.",
    ),
    Surface.CONFIG: SurfaceInfo(
        "Configuration Check panel",
        "The global checks: software version, lingering, notifications, general settings.",
    ),
    Surface.STARTUP: SurfaceInfo(
        "Full startup transcript",
        "Everything printed before scraping: Configuration Check, reminder, and "
        "Scraping panels stacked as one console.",
    ),
    Surface.STATUS: SurfaceInfo(
        "Health check (status)",
        "The per-scraper Service Status panels and global checks from ./scrooge-alert status.",
    ),
    Surface.PING: SurfaceInfo(
        "Notification check (ping)", "The delivery test results from ./scrooge-alert ping."
    ),
    Surface.SH_INSTALL: SurfaceInfo(
        "install.sh", "First-time installation transcript (venv, dependencies, systemd units)."
    ),
    Surface.SH_SETUP: SurfaceInfo(
        "setup.sh", "Preparing the development venv, dependencies, and repository checks."
    ),
    Surface.SH_PLUGIN_CREATE: SurfaceInfo(
        "plugin-create.sh", "Creating a guided additive source and optional test scaffold."
    ),
    Surface.SH_PLUGIN_CHECK: SurfaceInfo(
        "plugin-check.sh", "Verifying one target's contract, tests, and static analysis."
    ),
    Surface.SH_CHECK: SurfaceInfo(
        "check.sh", "Running the repository-wide static, shell, dependency, and test gates."
    ),
    Surface.SH_INSTALL_HOOKS: SurfaceInfo(
        "install-hooks.sh", "Enabling the repository-local versioned pre-push checks."
    ),
    Surface.SH_RUN: SurfaceInfo("run.sh", "Target selection and dispatch of the scraping runtime."),
    Surface.SH_PING: SurfaceInfo("ping.sh", "Dispatch of the notification test command."),
    Surface.SH_STATUS: SurfaceInfo("status.sh", "Dispatch of the installation health command."),
    Surface.SH_MIGRATE: SurfaceInfo(
        "migrate.sh", "Validating and migrating managed JSON documents."
    ),
    Surface.SH_SCHEDULE: SurfaceInfo(
        "schedule.sh", "Re-applying a configured execution interval to the installed timers."
    ),
    Surface.SH_ENABLE: SurfaceInfo("enable.sh", "Resuming the background timers."),
    Surface.SH_DISABLE: SurfaceInfo("disable.sh", "Pausing the background timers."),
    Surface.SH_STOP: SurfaceInfo("stop.sh", "Aborting a running scrape."),
    Surface.SH_UPDATE: SurfaceInfo("update.sh", "Updating the installation in place."),
    Surface.SH_UNINSTALL: SurfaceInfo(
        "uninstall.sh", "Removing services and the environment (user data kept)."
    ),
}


#: The curated tag vocabulary: tag -> one-line meaning. Tags are review/navigation
#: labels only (the gallery's --tag filter and the report's filter chips); they never
#: affect the snapshot gate. Every scenario tag must come from this set, and every
#: entry must be used by at least one scenario (no dead filter buttons) — both
#: guarded by ``tests/ui/test_ui_catalog.py``.
TAG_VOCABULARY: dict[str, str] = {
    "ok": "Healthy/expected outcome",
    "error": "An error or rejection state",
    "skipped": "An item or target skipped (skip flag, 404, invalid URL)",
    "help": "--help usage transcript",
    "retry": "Retry/back-off flow",
    "interrupt": "Ctrl-C / termination mid-run",
    "in_progress": "Transient state (spinner, sleep progress bar, running service)",
    "price_drop": "Price fell below target (notification flow)",
    "listing": "Listing-type scrape (multi-advert search rows)",
    "settings": "The settings section / an invalid setting",
    "target_config": "Target-configuration health (Config row / load failures)",
    "reminder": "Reminder cadence settings",
    "timer": "Systemd timer state / schedule drift",
    "last_run": "Last-execution verdict rows",
    "orphan": "Units left behind by a removed plugin",
    "catalog": "Plugin catalog/discovery problems",
    "system": "Locks, missing dependencies, missing prerequisites",
    "combined": "Several conditions in one panel",
    "layout": "Wrapping/truncation edge cases",
    "synthetic": "Renderer-only stress input that production cannot emit as one event",
}


@dataclass(frozen=True)
class OutputLog:
    """One background ``output.log`` artifact captured for a scenario."""

    path: str
    content: str


@dataclass(frozen=True)
class BuildResult:
    """The output of a scenario's ``build``: what to render and its border color.

    Attributes:
        renderable: A Rich ``Panel``, a ``panel.StatusPanelBuilder``, or (for the shell
            surfaces) a plain ``rich.text.Text`` transcript. The rendering layer knows
            how to paint any of them.
        border_color (str): The panel border color (``green``/``yellow``/``red``/``blue``),
            recorded in the snapshot header so a color regression is a one-line diff.
            Shell scenarios derive it from the exit code (0 -> green, else red).
        exit_code (int | None): The script's exit status for shell scenarios, recorded
            as a ``# exit:`` snapshot-header line. ``None`` for the Rich-panel surfaces.
        output_logs: Background file-log artifacts produced by the same scenario input.
            Empty for surfaces without a quiet/background execution equivalent.
    """

    renderable: Any
    border_color: str
    exit_code: int | None = None
    output_logs: tuple[OutputLog, ...] = ()


@dataclass(frozen=True)
class Scenario:
    """One catalogued UI case.

    Attributes:
        name (str): Unique snake_case identity within its surface; used in the snapshot
            filename ``<surface>__<name>.txt``.
        surface (Surface): Which UI surface this exercises.
        description (str): One-line human summary (shown as the gallery header).
        build (Callable[[], BuildResult]): Produces the renderable + border color. Called
            fresh each time (snapshot run and gallery run), so it must be deterministic.
        tags (tuple[str, ...]): Optional filter tags. Every tag must be a
            :data:`TAG_VOCABULARY` key (guarded by ``tests/ui/test_ui_catalog.py``).
        shell_debug (bool): Whether a shell scenario enabled underlying command
            diagnostics. False for every non-shell scenario.
        in_gallery (bool): Whether the scenario appears in the human-review surfaces
            (the terminal gallery and the HTML report). ``False`` marks a test-only
            scenario — it still snapshots and still feeds assertion tests (e.g. the
            STARTUP outside-panels guard), but reviewers are not shown a section that
            only duplicates panels covered elsewhere. The gallery still renders it
            when an explicit filter matches it: its surface via ``--surface``, or
            one of its tags via ``--tag``.
    """

    name: str
    surface: Surface
    description: str
    build: Callable[[], BuildResult]
    tags: tuple[str, ...] = ()
    shell_debug: bool = False
    in_gallery: bool = True

    @property
    def snapshot_key(self) -> str:
        """The stable ``<surface>__<name>`` key (snapshot filename stem)."""
        return f"{self.surface.value}__{self.name}"


_REGISTRY: list[Scenario] = []


def scenario(
    surface: Surface,
    name: str,
    description: str,
    tags: tuple[str, ...] = (),
    shell_debug: bool = False,
    in_gallery: bool = True,
):
    """Registers the decorated zero-arg ``build`` function as a :class:`Scenario`.

    Usage::

        @scenario(Surface.RUN, "success_drop", "Price drop below target")
        def _():
            return drive_run(script)

    The decorated function is returned unchanged, so it stays directly callable.
    Pass ``in_gallery=False`` for a test-only scenario (snapshotted and asserted on,
    but hidden from the gallery/report unless an explicit ``--surface``/``--tag``
    filter matches it).
    """

    def decorator(build_fn: Callable[[], BuildResult]) -> Callable[[], BuildResult]:
        _REGISTRY.append(
            Scenario(
                name=name,
                surface=surface,
                description=description,
                build=build_fn,
                tags=tuple(tags),
                shell_debug=shell_debug,
                in_gallery=in_gallery,
            )
        )
        return build_fn

    return decorator


def all_scenarios() -> list[Scenario]:
    """Returns every registered scenario, in registration order."""
    return list(_REGISTRY)
