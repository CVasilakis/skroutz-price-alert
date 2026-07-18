"""Stable machine-readable command line bridge for POSIX management scripts."""

from __future__ import annotations

import argparse
import sys

from core.constants import CONFIG_DIR
from core.scrapers.base.settings import SUPPORTED_INTERVALS
from core.scrapers.registry import ScraperRegistry


def _plugins(view: str) -> None:
    for target in ScraperRegistry.registered_targets():
        plugin = ScraperRegistry.get_plugin(target)
        if view == "targets":
            print(target)
        elif view == "examples":
            print(f"{target}\t{plugin.example_config_path}")
        elif view == "requirements" and plugin.requirements_path:
            print(f"{target}\t{plugin.requirements_path}")
        elif view == "all":
            print(
                f"{target}\t{plugin.display_name}\t{plugin.config_filename}\t"
                f"{plugin.example_config_path}\t{plugin.requirements_path or ''}"
            )


def _schedules(view: str, config_dir: str) -> None:
    for target in ScraperRegistry.registered_targets():
        schedule = ScraperRegistry.resolve_schedule(target, config_dir)
        value = schedule.on_calendar if view == "calendar" else schedule.status
        print(f"{target}\t{value}")


def _diagnose() -> int:
    try:
        targets = ScraperRegistry.registered_targets()
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")
        return 1
    print(f"  (discovery succeeded on retry: {len(targets)} scraper(s) registered)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.scrapers.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plugins = subparsers.add_parser("plugins")
    plugins.add_argument(
        "--view",
        choices=("all", "targets", "examples", "requirements"),
        default="all",
    )
    schedules = subparsers.add_parser("schedules")
    schedules.add_argument("--view", choices=("calendar", "status"), default="calendar")
    schedules.add_argument("--config-dir", default=CONFIG_DIR)
    subparsers.add_parser("intervals")
    subparsers.add_parser("diagnose")

    args = parser.parse_args(argv)
    if args.command == "plugins":
        _plugins(args.view)
    elif args.command == "schedules":
        _schedules(args.view, args.config_dir)
    elif args.command == "intervals":
        print(", ".join(SUPPORTED_INTERVALS))
    else:
        return _diagnose()
    return 0


if __name__ == "__main__":
    sys.exit(main())
