"""Stable machine-readable bridge for POSIX management scripts."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from core.constants import CONFIG_DIR
from core.exceptions import ConfigFileError, PluginError, StorageFileError
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.framework.intervals import SUPPORTED_INTERVALS, oncalendar_for
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.settings import KEY_INTERVAL
from core.scrapers.tooling.check import check_plugin
from core.settings import ResolvedSetting, SettingStatus


@dataclass(frozen=True)
class ScheduleResolution:
    on_calendar: str
    status: SettingStatus


def _tsv_row(*fields: str) -> str:
    for field in fields:
        if any(separator in field for separator in ("\t", "\r", "\n")):
            raise ValueError("tooling TSV fields must be single-line and tab-free")
    return "\t".join(fields)


def resolve_schedule(plugin: RegisteredPlugin, config_dir: str) -> ScheduleResolution:
    """Resolve a plugin schedule from one strict target-config read."""
    path = Path(config_dir) / plugin.config_filename
    interval_spec = plugin.setting(KEY_INTERVAL)
    if not path.exists():
        interval = ResolvedSetting(plugin.default_interval, SettingStatus.NO_CONFIG)
    else:
        settings = TargetConfigLoader(plugin, config_dir).load_settings()
        interval = settings.resolved(interval_spec)
    canonical = (
        interval.value
        if interval.status in (SettingStatus.OK, SettingStatus.DEFAULT)
        else plugin.default_interval
    )
    return ScheduleResolution(oncalendar_for(canonical), interval.status)


def catalog_rows(catalog: PluginCatalog) -> tuple[str, ...]:
    """Return immutable, config-independent plugin metadata for shell scripts."""
    return tuple(
        _tsv_row(
            plugin.target,
            plugin.display_name,
            plugin.example_config_path,
            plugin.requirements_path or "",
        )
        for plugin in catalog.plugins
    )


def schedule_rows(catalog: PluginCatalog, config_dir: str) -> tuple[str, ...]:
    """Return an isolated schedule result for every registered plugin."""
    rows: list[str] = []
    for plugin in catalog.plugins:
        try:
            schedule = resolve_schedule(plugin, config_dir)
        except ConfigFileError as exc:
            rows.append(_tsv_row(plugin.target, "", "error", str(exc)))
        else:
            rows.append(
                _tsv_row(
                    plugin.target,
                    schedule.on_calendar,
                    schedule.status.value,
                    "",
                )
            )
    return tuple(rows)


def requirements(catalog: PluginCatalog) -> tuple[str, ...]:
    """Return config-independent target/private-requirement pairs for dev tooling."""
    return tuple(
        _tsv_row(plugin.target, plugin.requirements_path or "") for plugin in catalog.plugins
    )


def _diagnose() -> int:
    try:
        catalog = PluginCatalog.discover()
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")
        return 1
    print(f"  Plugin discovery succeeds now ({len(catalog.plugins)} scraper(s) registered).")
    return 0


def _plugin_check_failure(exc: Exception) -> str:
    """Render one terminal-safe diagnostic record for the shell wrapper."""
    printable = "".join(character if character.isprintable() else " " for character in str(exc))
    detail = " ".join(printable.split()) or type(exc).__name__
    return f"Plugin check failed: {detail}"


def main(argv: list[str] | None = None, *, catalog: PluginCatalog | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.scrapers.tooling.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("catalog")
    schedules_parser = subparsers.add_parser("schedules")
    schedules_parser.add_argument("--config-dir", default=CONFIG_DIR)
    subparsers.add_parser("intervals")
    subparsers.add_parser("requirements")
    subparsers.add_parser("diagnose")
    plugin_check = subparsers.add_parser("plugin-check")
    plugin_check.add_argument("target")

    args = parser.parse_args(argv)
    if args.command == "catalog":
        for row in catalog_rows(catalog or PluginCatalog.discover()):
            print(row)
    elif args.command == "schedules":
        for row in schedule_rows(catalog or PluginCatalog.discover(), args.config_dir):
            print(row)
    elif args.command == "intervals":
        print(", ".join(SUPPORTED_INTERVALS))
    elif args.command == "requirements":
        for row in requirements(catalog or PluginCatalog.discover()):
            print(row)
    elif args.command == "diagnose":
        return _diagnose()
    else:
        try:
            report = check_plugin(args.target, catalog)
        except (PluginError, StorageFileError, RuntimeError, ValueError) as exc:
            print(_plugin_check_failure(exc), file=sys.stderr)
            return 1
        for label in report.checks:
            print(f"ok\t{label}")
        print(f"tests\t{int(report.has_tests)}")
        for warning in report.warnings:
            print(_tsv_row("warning", warning))
    return 0


if __name__ == "__main__":
    sys.exit(main())
