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
from core.scrapers.framework.setting_specs import KEY_INTERVAL
from core.scrapers.tooling.check import check_plugin
from core.settings import ResolvedSetting, SettingStatus


@dataclass(frozen=True)
class ScheduleResolution:
    """One target's effective timer cadence and where that value came from.

    The status travels with the value because the shell scripts must distinguish a
    configured cadence from a fallback: an invalid ``execution_interval`` still
    produces a working timer, and the user is warned rather than left unscheduled.
    """

    on_calendar: str
    """The rendered systemd ``OnCalendar`` expression."""

    status: SettingStatus
    """Whether the interval was configured, defaulted, or invalid."""


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
    """Return immutable, config-independent plugin metadata for shell scripts.

    One row per registered plugin, in catalog order. Columns, numbered as the
    consuming tab-separated ``awk`` in ``scripts/lib/common.sh`` addresses them:

    1. ``target`` - the snake_case identity, also the systemd unit-name stem.
    2. ``display_name`` - the human label the scripts print.
    3. ``example_config_path`` - always populated.
    4. ``requirements_path`` - empty when the plugin declares no private
       dependencies. The shell filters on that emptiness rather than probing the
       filesystem, so the column is emitted even when there is nothing to install.

    Nothing here reads a target config, which is what keeps identity and static
    paths available to the scripts while a target's configuration is broken.
    """
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
    """Return an isolated schedule result for every registered plugin.

    One row per registered plugin, in catalog order; a target whose config could
    not be read fails alone and never suppresses another target's row. Columns,
    numbered as ``scripts/lib/common.sh`` addresses them:

    1. ``target``.
    2. ``on_calendar`` - the rendered timer expression, empty on an error row.
    3. ``status`` - the vocabulary the scripts branch on. Each consuming command
       owns what to do with a given value and they differ deliberately;
       ``scripts/lib/common.sh`` records the policies beside the accessors.
    4. ``error`` - the presentation-safe ``ConfigFileError`` message on an error
       row, collapsed to one line, empty otherwise.

    The status vocabulary has five values. ``error`` is this report's own state,
    not a :class:`SettingStatus`: the config read raised, so no interval was
    resolved at all. The other four are the ``execution_interval`` resolution
    status: ``ok`` (configured and decoded), ``default`` (no value set),
    ``invalid`` (a value was set but rejected), and ``nocfg`` (no config file on
    disk). ``missing`` cannot reach a row, because ``execution_interval`` is
    optional; a plugin's own *required* setting failing validation raises
    ``ConfigFileError`` and lands on an ``error`` row instead.

    ``nocfg`` therefore means the file is absent, and only that. A file that
    exists without a ``settings`` block resolves against an empty block and
    reports ``default``, while ``"settings": null`` is a schema violation and
    reports ``error``.
    """
    rows: list[str] = []
    for plugin in catalog.plugins:
        try:
            schedule = resolve_schedule(plugin, config_dir)
        except ConfigFileError as exc:
            # The message is the only free-form field in either snapshot, so it is
            # the only one that could carry a separator into _tsv_row and raise.
            # Collapsing it here keeps a broken target on its own error row instead
            # of failing the whole report, which is what that row exists to prevent.
            rows.append(_tsv_row(plugin.target, "", "error", " ".join(str(exc).split())))
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
    """Return config-independent target/private-requirement pairs for dev tooling.

    Two columns, ``target`` and ``requirements_path``, the latter empty for a
    plugin with no private dependencies. Unlike the shell's catalog accessor, the
    empty column is not filtered out here: ``scripts/dev/setup.sh`` and the
    per-plugin CI dependency job iterate every target and read an empty path as
    "core dependencies only".
    """
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
    """Emit the TSV snapshots the shell scripts read, or a per-target schedule report.

    The bridge between the Python catalog and the POSIX scripts: the shell never
    imports plugin code or parses config itself, it reads these rows. Fields are
    validated to be single-line and tab-free, since one stray separator would
    silently misalign a downstream column.

    The two snapshots are deliberately separate. The catalog snapshot is
    config-independent, so identity and static paths stay available even when a
    target's configuration is broken; only the schedule report can carry a
    per-target configuration error. Each row builder documents its own columns,
    and ``scripts/lib/common.sh`` repeats them as a legend beside the field
    numbers its accessors use.

    ``plugin-check`` is not one of those snapshots but a contributor-tooling
    record stream, keyed by its first column rather than positional: ``ok`` per
    passed check, exactly one ``tests`` row carrying ``0`` or ``1``, and a
    ``warning`` row per advisory. ``scripts/dev/plugin-check.sh`` ignores
    unrecognized kinds, so debug output interleaved by a wrapper stays harmless.
    """
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
