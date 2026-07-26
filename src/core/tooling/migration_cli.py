"""Command-line entry point for project-wide JSON schema migration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.constants import (
    BASE_DIR,
    EXIT_CODE_NOTIFICATION_CONFIG_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_STORAGE_ERROR,
)
from core.scrapers.framework.catalog import PluginCatalog
from core.tooling.migration import (
    STATUS_FAILED,
    STATUS_MIGRATED,
    STATUS_MISSING,
    MigrationOutcome,
    MigrationRunner,
)


def _exit_code(outcomes: tuple[MigrationOutcome, ...], *, check: bool) -> int:
    failed = {outcome.family for outcome in outcomes if outcome.status == STATUS_FAILED}
    if "target_config" in failed:
        return EXIT_CODE_PRODUCTS_ERROR
    if "general_config" in failed:
        return EXIT_CODE_NOTIFICATION_CONFIG_ERROR
    if failed & {"scraper_state", "reminder_state"}:
        return EXIT_CODE_STORAGE_ERROR
    if check and any(outcome.status == STATUS_MIGRATED for outcome in outcomes):
        return 1
    return 0


def _print_human(outcomes: tuple[MigrationOutcome, ...], recovery: Path | None) -> None:
    for outcome in outcomes:
        if outcome.status == STATUS_MISSING:
            continue
        suffix = f": {outcome.detail}" if outcome.detail else ""
        print(f"{outcome.path}: {outcome.status}{suffix}")
    if recovery is not None:
        print(f"Recovery copies retained at: {recovery}", file=sys.stderr)


def _print_machine(outcomes: tuple[MigrationOutcome, ...], recovery: Path | None) -> None:
    for outcome in outcomes:
        detail = outcome.detail.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("\t".join((outcome.family, outcome.target, outcome.status, outcome.path, detail)))
    if recovery is not None:
        print(f"recovery\tgeneral\tretained\t{recovery}\t")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./scripts/migrate.sh",
        description="Validate and migrate every known Scrooge Alert JSON document.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "validate and report without modifying managed JSON documents; "
            "lock metadata may be created"
        ),
    )
    parser.add_argument("--machine", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", default=BASE_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        runner = MigrationRunner(args.root, PluginCatalog.discover())
        outcomes = runner.run(check=args.check)
    except Exception as exc:
        print(f"Migration could not start: {exc}", file=sys.stderr)
        return 1
    if args.machine:
        _print_machine(outcomes, runner.recovery_path)
    else:
        _print_human(outcomes, runner.recovery_path)
    return _exit_code(outcomes, check=args.check)


if __name__ == "__main__":
    sys.exit(main())
