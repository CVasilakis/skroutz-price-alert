"""Preflight target loading: the run's single storage read/validation phase.

Holds the :class:`TargetLoad` outcome record and :func:`load_targets`, the one place a
target's products config is opened and validated before a run. This is orchestration-side
work (file I/O + registry cache-warming), deliberately outside :mod:`core.ui` — the UI
package only *renders* these outcomes (``ui.config_check.config_view`` builds the 'Config'
row from a ``TargetLoad``'s fields). The mode-specific gating/rendering entry point named
``preflight()`` stays in :mod:`core.ui.config_check`, since its interactive half draws the
Configuration Check panel.
"""

from dataclasses import dataclass, field

from core.exceptions import StorageFileError, PluginDependencyError
from core.scrapers.registry import ScraperRegistry


@dataclass
class TargetLoad:
    """Outcome of loading a single target's storage during the preflight load phase.

    Attributes:
        target (str): The target name.
        count (int): The number of loaded items (0 when the load failed).
        faulty_indices (list[int]): 1-based indices of items failing validation.
        error (str | None): The failure message if the storage could not be loaded.

    Note:
        This outcome is no longer rendered on the shared 'Configuration Check' panel.
        Both it and the ``settings`` block are surfaced per-scraper: the products-config
        health as a 'Config' row (built via ``ui.config_check.config_view``) and the
        settings section atop each Service Status panel (``--status``) and Scraping
        panel (a run).
    """
    target: str
    count: int = 0
    faulty_indices: list[int] = field(default_factory=list)
    error: str | None = None


def load_targets(registry: ScraperRegistry, targets: list) -> list[TargetLoad]:
    """Loads every target's storage exactly once — the single read/validation point.

    The managers are cached in the registry, so the orchestrator later reuses the
    very same in-memory snapshot without re-reading any file. This is the only
    place a config file is opened for validation.

    Args:
        registry (ScraperRegistry): The registry used to resolve and cache managers.
        targets (list): The targets to load.

    Returns:
        list[TargetLoad]: One outcome per resolvable target, in the given order
            (targets without a registered plugin are skipped).
    """
    results: list[TargetLoad] = []
    for target in targets:
        try:
            manager = registry.get_manager(target)
        except ValueError:
            continue
        except PluginDependencyError:
            # The plugin's storage layer needs dependencies that are not
            # installed. Skip it here so preflight does not crash; the
            # orchestrator surfaces the actionable './install.sh --<plugin>'
            # message per-target and lets the other targets proceed, matching
            # how a missing transport (client) dependency is handled at runtime.
            continue
        try:
            manager.load()
            results.append(TargetLoad(
                target, manager.get_item_count(), manager.get_faulty_indices(),
            ))
        except StorageFileError as e:
            results.append(TargetLoad(target, error=str(e)))
    return results
