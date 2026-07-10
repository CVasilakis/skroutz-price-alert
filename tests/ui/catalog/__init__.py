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
# The import order is the gallery/report section order (Python surfaces in workflow
# order, then the shell scripts in lifecycle order) and must match the Surface member
# order — guarded by tests/ui/test_ui_catalog.py.
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

#: Every registered scenario across all surfaces, in registration order.
ALL_SCENARIOS = all_scenarios()

__all__ = [
    "ALL_SCENARIOS", "Scenario", "Surface", "SurfaceInfo", "SURFACE_INFO",
    "TAG_VOCABULARY", "scenario", "BuildResult",
]
