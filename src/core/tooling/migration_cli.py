"""Command-line entry point for project-wide JSON schema migration."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.constants import BASE_DIR
from core.exceptions import LockStorageError
from core.exit_status import ExitStatus
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
        return ExitStatus.TARGET_CONFIG_ERROR
    if "general_config" in failed:
        return ExitStatus.NOTIFICATION_CONFIG_ERROR
    if failed & {"scraper_state", "reminder_state"}:
        return ExitStatus.STORAGE_ERROR
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
    """Emit the tab-separated migration report the POSIX scripts parse.

    One row per inspected document, in inspection order, with a single trailing
    recovery row when a partially applied migration retained copies. Columns,
    numbered as ``scripts/dev/migrate.sh`` and ``scripts/update.sh`` address them:

    1. ``family`` - ``general_config``, ``target_config``, ``scraper_state``,
       ``reminder_state``, or ``recovery`` on the trailing row.
    2. ``target`` - the owning scraper, or ``general`` for a project-wide
       document and for the recovery row.
    3. ``result`` - ``current``, ``migrated``, ``failed``, or ``missing`` for a
       document, and ``retained`` for the recovery row.
    4. ``path`` - the document, or the retained recovery directory.
    5. ``detail`` - free-form and possibly empty. Every separator is collapsed to
       a space, because one stray tab or newline would misalign the row the shell
       reads positionally.

    Unlike the human rendering, a ``missing`` document still gets a row: the
    consumers decide what an absent document means to them, and both currently
    ignore it. The exit status, not the rows, is what tells a caller whether the
    run failed; ``scripts/update.sh`` needs both, since it maps each failed row
    onto the target whose timer must stay disabled.
    """
    for outcome in outcomes:
        detail = outcome.detail.replace("\t", " ").replace("\r", " ").replace("\n", " ")
        print("\t".join((outcome.family, outcome.target, outcome.status, outcome.path, detail)))
    if recovery is not None:
        print(f"recovery\tgeneral\tretained\t{recovery}\t")


def main(argv: list[str] | None = None) -> int:
    """Run or check every schema migration and report one line per document.

    Invoked by ``./scrooge-alert update`` before timers are reactivated, and by
    contributors as ``--check`` to validate without modifying anything.
    """
    parser = argparse.ArgumentParser(
        prog="./scripts/dev/migrate.sh",
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
    # Hidden because neither is a user interface: --machine is the shell wrapper's
    # parsing contract (documented on _print_machine) and --root exists so that
    # wrapper can name the checkout it was invoked from.
    parser.add_argument("--machine", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", default=BASE_DIR, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        runner = MigrationRunner(args.root, PluginCatalog.discover())
        outcomes = runner.run(check=args.check)
    except LockStorageError as exc:
        print(f"Migration lock storage failed: {exc}", file=sys.stderr)
        return ExitStatus.STORAGE_ERROR
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
