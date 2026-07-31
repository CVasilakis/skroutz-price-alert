"""The scenario catalog: the single source of truth consumed by the snapshot tests and
the gallery.

Importing this package imports every ``*_scenarios.py`` module (which registers its
scenarios via the :func:`scenario` decorator) and exposes the aggregated
:data:`ALL_SCENARIOS`.
"""

# Importing each module runs its @scenario decorators, populating the registry.
# Import order is NOT significant: ALL_SCENARIOS below is sorted into Surface member
# order, so the enum is the single source of truth for section order.
from ui.catalog import (
    config_scenarios,  # noqa: F401
    e2e_run_scenarios,  # noqa: F401
    ping_scenarios,  # noqa: F401
    run_scenarios,  # noqa: F401
    sh_check_scenarios,  # noqa: F401
    sh_disable_scenarios,  # noqa: F401
    sh_enable_scenarios,  # noqa: F401
    sh_install_hooks_scenarios,  # noqa: F401
    sh_install_scenarios,  # noqa: F401
    sh_migrate_scenarios,  # noqa: F401
    sh_plugin_check_scenarios,  # noqa: F401
    sh_plugin_create_scenarios,  # noqa: F401
    sh_run_scenarios,  # noqa: F401
    sh_schedule_scenarios,  # noqa: F401
    sh_setup_scenarios,  # noqa: F401
    sh_stop_scenarios,  # noqa: F401
    sh_uninstall_scenarios,  # noqa: F401
    sh_update_scenarios,  # noqa: F401
    startup_scenarios,  # noqa: F401
    status_scenarios,  # noqa: F401
)
from ui.catalog._base import (
    BACKGROUND_SURFACES,
    SURFACE_INFO,
    TAG_VOCABULARY,
    BuildResult,
    OutputLog,
    Scenario,
    Surface,
    SurfaceInfo,
    all_scenarios,
    scenario,
)

#: Every registered scenario across all surfaces, in display order: sections follow
#: the Surface member order (the single source of truth for the gallery/report), and
#: scenarios keep their registration order within a surface (sorted() is stable).
_SURFACE_ORDER = {s: i for i, s in enumerate(Surface)}
ALL_SCENARIOS = sorted(all_scenarios(), key=lambda sc: _SURFACE_ORDER[sc.surface])

__all__ = [
    "ALL_SCENARIOS",
    "BACKGROUND_SURFACES",
    "Scenario",
    "Surface",
    "SurfaceInfo",
    "SURFACE_INFO",
    "TAG_VOCABULARY",
    "scenario",
    "BuildResult",
    "OutputLog",
]
