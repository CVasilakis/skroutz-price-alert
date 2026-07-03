"""The scenario abstraction and registry — the single source of truth's backbone.

A :class:`Scenario` pairs a name/surface/description with a zero-argument ``build``
callable that returns a :class:`BuildResult` (a Rich renderable plus its computed border
color). Scenarios self-register via the :func:`scenario` decorator; the catalog package
imports every ``*_scenarios.py`` module to populate the registry and exposes the
aggregated :data:`ALL_SCENARIOS`.

This module is the import leaf of the catalog: it depends only on stdlib, so the driver
and rendering layers can import :class:`BuildResult` from here without a cycle.
"""

from dataclasses import dataclass
from enum import Enum
from collections.abc import Callable
from typing import Any


class Surface(Enum):
    """The UI surface a scenario belongs to (also the snapshot-filename prefix)."""
    RUN = "run"        # the interactive scraping panel (tui.InteractiveExecutionStrategy)
    STATUS = "status"  # a --status panel (service / not-installed / orphan)
    PING = "ping"      # the --ping Notification Check Results panel
    CONFIG = "config"  # the shared Configuration Check panel


@dataclass(frozen=True)
class BuildResult:
    """The output of a scenario's ``build``: what to render and its border color.

    Attributes:
        renderable: A Rich ``Panel`` or a ``panel.StatusPanelBuilder``. The rendering
            layer knows how to paint either.
        border_color (str): The panel border color (``green``/``yellow``/``red``/``blue``),
            recorded in the snapshot header so a color regression is a one-line diff.
    """
    renderable: Any
    border_color: str


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
        tags (tuple[str, ...]): Optional filter tags (e.g. ``"retry"``, ``"interrupt"``).
    """
    name: str
    surface: Surface
    description: str
    build: Callable[[], BuildResult]
    tags: tuple[str, ...] = ()

    @property
    def snapshot_key(self) -> str:
        """The stable ``<surface>__<name>`` key (snapshot filename stem)."""
        return f"{self.surface.value}__{self.name}"


_REGISTRY: list[Scenario] = []


def scenario(surface: Surface, name: str, description: str, tags: tuple[str, ...] = ()):
    """Registers the decorated zero-arg ``build`` function as a :class:`Scenario`.

    Usage::

        @scenario(Surface.RUN, "success_drop", "Price drop below target")
        def _():
            return drive_run(script)

    The decorated function is returned unchanged, so it stays directly callable.
    """
    def decorator(build_fn: Callable[[], BuildResult]) -> Callable[[], BuildResult]:
        _REGISTRY.append(Scenario(
            name=name, surface=surface, description=description,
            build=build_fn, tags=tuple(tags),
        ))
        return build_fn
    return decorator


def all_scenarios() -> list[Scenario]:
    """Returns every registered scenario, in registration order."""
    return list(_REGISTRY)
