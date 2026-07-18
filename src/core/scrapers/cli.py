"""Stable machine-readable bridge for POSIX management scripts."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from core.constants import CONFIG_DIR
from core.scrapers.check import check_plugin
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.intervals import SUPPORTED_INTERVALS, oncalendar_for
from core.scrapers.registry import PluginCatalog, RegisteredPlugin
from core.scrapers.settings import KEY_INTERVAL
from core.settings import ResolvedSetting, SettingStatus


@dataclass(frozen=True)
class ScheduleResolution:
    on_calendar: str
    status: SettingStatus


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


def manifest(catalog: PluginCatalog, config_dir: str) -> tuple[str, ...]:
    """Return the single six-column TSV manifest consumed by shell scripts."""
    rows: list[str] = []
    for plugin in catalog.plugins:
        schedule = resolve_schedule(plugin, config_dir)
        rows.append("\t".join((
            plugin.target,
            plugin.display_name,
            plugin.example_config_path,
            plugin.requirements_path or "",
            schedule.on_calendar,
            schedule.status.value,
        )))
    return tuple(rows)


def _diagnose() -> int:
    try:
        catalog = PluginCatalog.discover()
    except Exception as exc:
        print(f"  {type(exc).__name__}: {exc}")
        return 1
    print(f"  (discovery succeeded on retry: {len(catalog.plugins)} scraper(s) registered)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m core.scrapers.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument("--config-dir", default=CONFIG_DIR)
    subparsers.add_parser("intervals")
    subparsers.add_parser("diagnose")
    plugin_check = subparsers.add_parser("plugin-check")
    plugin_check.add_argument("target")

    args = parser.parse_args(argv)
    if args.command == "manifest":
        for row in manifest(PluginCatalog.discover(), args.config_dir):
            print(row)
    elif args.command == "intervals":
        print(", ".join(SUPPORTED_INTERVALS))
    elif args.command == "diagnose":
        return _diagnose()
    else:
        try:
            checks = check_plugin(args.target)
        except (RuntimeError, ValueError) as exc:
            print(f"Plugin check failed: {exc}", file=sys.stderr)
            return 1
        for label in checks:
            print(f"ok\t{label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
