"""Public service for creating a validated plugin scaffold."""

from pathlib import Path

from core.scrapers.tooling.scaffold.contracts import (
    ScaffoldRequest,
    ScaffoldResult,
    validate_request,
)
from core.scrapers.tooling.scaffold.storage import create_plugin as _commit_plugin


def create_plugin(repo_root: Path, request: ScaffoldRequest) -> ScaffoldResult:
    """Validate, render, and commit one additive plugin scaffold."""
    return _commit_plugin(repo_root, validate_request(request))


__all__ = ["create_plugin"]
