"""The scenario catalog: the single source of truth consumed by the snapshot tests and
the gallery.

Importing this package imports every ``*_scenarios.py`` module (which registers its
scenarios via the :func:`scenario` decorator) and exposes the aggregated
:data:`ALL_SCENARIOS`.
"""

from ui.catalog._base import (
    Scenario, Surface, SurfaceInfo, SURFACE_INFO, TAG_VOCABULARY,
    scenario, all_scenarios, BuildResult,
)

# Importing each module runs its @scenario decorators, populating the registry.
# Import order is NOT significant: ALL_SCENARIOS below is sorted into Surface member
# order, so the enum is the single source of truth for section order.
from ui.catalog import run_scenarios          # noqa: F401
from ui.catalog import e2e_run_scenarios      # noqa: F401
from ui.catalog import config_scenarios       # noqa: F401
from ui.catalog import startup_scenarios      # noqa: F401
from ui.catalog import status_scenarios       # noqa: F401
from ui.catalog import ping_scenarios         # noqa: F401
from ui.catalog import sh_install_scenarios   # noqa: F401
from ui.catalog import sh_run_scenarios       # noqa: F401
from ui.catalog import sh_schedule_scenarios  # noqa: F401
from ui.catalog import sh_enable_scenarios    # noqa: F401
from ui.catalog import sh_disable_scenarios   # noqa: F401
from ui.catalog import sh_stop_scenarios      # noqa: F401
from ui.catalog import sh_update_scenarios    # noqa: F401
from ui.catalog import sh_uninstall_scenarios # noqa: F401

#: Every registered scenario across all surfaces, in display order: sections follow
#: the Surface member order (the single source of truth for the gallery/report), and
#: scenarios keep their registration order within a surface (sorted() is stable).
_SURFACE_ORDER = {s: i for i, s in enumerate(Surface)}
ALL_SCENARIOS = sorted(all_scenarios(), key=lambda sc: _SURFACE_ORDER[sc.surface])

__all__ = [
    "ALL_SCENARIOS", "Scenario", "Surface", "SurfaceInfo", "SURFACE_INFO",
    "TAG_VOCABULARY", "scenario", "BuildResult",
]
