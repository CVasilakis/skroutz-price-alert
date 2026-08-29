"""Application coordination for best-effort technical diagnostic persistence."""

from __future__ import annotations

from dataclasses import replace

from core.application.preflight import TargetConfigLoad
from core.general import GeneralConfigLoad
from core.infrastructure.logging import try_save_diagnostic


def record_general_diagnostic(load: GeneralConfigLoad) -> GeneralConfigLoad:
    """Record a general-config diagnostic and return its immutable write outcome."""
    if not load.diagnostic:
        return load
    saved = try_save_diagnostic(load.diagnostic)
    return replace(load, diagnostic_saved=saved)


def record_target_load_diagnostic(load: TargetConfigLoad) -> bool | None:
    """Record target-configuration diagnostics without producing terminal output."""
    diagnostics = [
        detail
        for detail in (
            load.failure.diagnostic if load.failure is not None else None,
            load.row_diagnostic,
        )
        if detail
    ]
    if not diagnostics:
        return None
    return try_save_diagnostic("\n\n".join(diagnostics), target_name=load.target)


__all__ = ["record_general_diagnostic", "record_target_load_diagnostic"]
