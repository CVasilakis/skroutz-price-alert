"""Cross-family migration inventory, validation, and safe persistence."""

from __future__ import annotations

import json
import os
import shutil
import stat
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout

from core.general.migrations import GENERAL_CONFIG_MIGRATIONS, REMINDER_STATE_MIGRATIONS
from core.infrastructure.migration import (
    MigrationError,
    MigrationPlan,
    migrate_document,
    schema_version,
)
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.framework.migrations import (
    SCRAPER_STATE_MIGRATIONS,
    PluginMigrationDeclarationError,
    load_plugin_config_migrations,
    target_config_plan,
)
from core.scrapers.framework.model import RegisteredPlugin

STATUS_CURRENT = "current"
STATUS_MIGRATED = "migrated"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class MigrationOutcome:
    family: str
    target: str
    path: str
    status: str
    detail: str = ""


@dataclass(frozen=True)
class _SourceSnapshot:
    data: bytes
    device: int
    inode: int
    mode: int


def _read_document(path: Path) -> tuple[dict[str, Any], _SourceSnapshot]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise MigrationError("path must be a regular file, not a symlink or special file")
    data = path.read_bytes()
    try:
        document = json.loads(data)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError(f"invalid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise MigrationError("top-level JSON value must be an object")
    return document, _SourceSnapshot(
        data,
        info.st_dev,
        info.st_ino,
        stat.S_IMODE(info.st_mode),
    )


def _unchanged(path: Path, snapshot: _SourceSnapshot) -> bool:
    try:
        info = path.lstat()
        return (
            stat.S_ISREG(info.st_mode)
            and not stat.S_ISLNK(info.st_mode)
            and info.st_dev == snapshot.device
            and info.st_ino == snapshot.inode
            and path.read_bytes() == snapshot.data
        )
    except OSError:
        return False


def _serialize(document: dict[str, Any]) -> bytes:
    return (json.dumps(document, indent=2) + "\n").encode()


def _replace_atomically(path: Path, data: bytes, mode: int) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.migration.", dir=path.parent)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


@contextmanager
def _held_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(path, timeout=0):
            yield
    except Timeout as exc:
        raise MigrationError(f"another process holds lock {path}") from exc


class MigrationRunner:
    """Migrate all known documents with independent atomic commits."""

    def __init__(self, root: str | os.PathLike[str], catalog: PluginCatalog) -> None:
        self.root = Path(root)
        self.catalog = catalog
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"
        self.logs_dir = self.root / "logs"
        self._recovery: Path | None = None

    @property
    def recovery_path(self) -> Path | None:
        return self._recovery

    def _ensure_recovery(self) -> Path:
        if self._recovery is None:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            self._recovery = Path(
                tempfile.mkdtemp(prefix=".migration-recovery.", dir=self.state_dir)
            )
            self._recovery.chmod(0o700)
        return self._recovery

    def _backup(self, path: Path, snapshot: _SourceSnapshot) -> None:
        relative = path.relative_to(self.root)
        backup = self._ensure_recovery() / relative
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, snapshot.mode)
        os.fchmod(descriptor, snapshot.mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(snapshot.data)
            stream.flush()
            os.fsync(stream.fileno())

    def _run_one(
        self,
        family: str,
        target: str,
        path: Path,
        plan: MigrationPlan,
        *,
        check: bool,
    ) -> MigrationOutcome:
        shown = str(path.relative_to(self.root))
        try:
            if not path.exists() and not path.is_symlink():
                return MigrationOutcome(family, target, shown, STATUS_MISSING)
            document, snapshot = _read_document(path)
            original_version = schema_version(document)
            migrated = migrate_document(document, plan)
            if original_version == plan.current_version:
                return MigrationOutcome(family, target, shown, STATUS_CURRENT)
            rendered = _serialize(migrated)
            if check:
                return MigrationOutcome(
                    family,
                    target,
                    shown,
                    STATUS_MIGRATED,
                    f"pending v{original_version} to v{plan.current_version}",
                )
            if not _unchanged(path, snapshot):
                raise MigrationError("file changed while migration was being prepared")
            self._backup(path, snapshot)
            if not _unchanged(path, snapshot):
                raise MigrationError("file changed before atomic replacement")
            _replace_atomically(path, rendered, snapshot.mode)
            return MigrationOutcome(
                family,
                target,
                shown,
                STATUS_MIGRATED,
                f"v{original_version} to v{plan.current_version}",
            )
        except Exception as exc:
            return self._failed_outcome(family, target, shown, exc)

    @staticmethod
    def _failed_outcome(
        family: str,
        target: str,
        shown: str,
        exc: Exception,
    ) -> MigrationOutcome:
        if family == "target_config":
            advice = (
                "Original preserved; compare it with "
                f"src/core/scrapers/plugins/{target}/config.example.json."
            )
        elif family == "general_config":
            advice = "Original preserved; compare it with src/core/general/config.example.json."
        elif family == "scraper_state":
            advice = (
                f"Original preserved; repair it or delete {shown} to recreate it "
                "(stored check and alert history will be lost)."
            )
        elif family == "reminder_state":
            advice = (
                f"Original preserved; repair it or delete {shown} to recreate it "
                "(the stored reminder timestamp and scheduling history will be lost, "
                "and a reminder may be sent again)."
            )
        else:
            advice = "Original preserved."
        detail = str(exc)
        separator = " " if detail.endswith((".", "!", "?")) else ". "
        return MigrationOutcome(
            family,
            target,
            shown,
            STATUS_FAILED,
            f"{detail}{separator}{advice}",
        )

    def _run_target_locked(
        self,
        plugin: RegisteredPlugin,
        outcomes: list[MigrationOutcome],
        *,
        check: bool,
    ) -> None:
        config_path = self.config_dir / plugin.config_filename
        config_shown = f"config/{plugin.config_filename}"
        try:
            config_missing = not config_path.exists() and not config_path.is_symlink()
            if config_missing:
                outcomes.append(
                    MigrationOutcome(
                        "target_config",
                        plugin.target,
                        config_shown,
                        STATUS_MISSING,
                    )
                )
            else:
                loader = TargetConfigLoader(plugin, str(self.config_dir))
                try:
                    private = load_plugin_config_migrations(plugin)
                except PluginMigrationDeclarationError as exc:
                    raise MigrationError(str(exc)) from exc

                def validate_target(document: dict[str, Any]) -> None:
                    loader.load_document(document)

                config_plan = target_config_plan(validate_target, private)
                outcomes.append(
                    self._run_one(
                        "target_config",
                        plugin.target,
                        config_path,
                        config_plan,
                        check=check,
                    )
                )
        except Exception as exc:
            outcomes.append(
                self._failed_outcome(
                    "target_config",
                    plugin.target,
                    config_shown,
                    exc,
                )
            )

        outcomes.append(
            self._run_one(
                "scraper_state",
                plugin.target,
                self.state_dir / f"{plugin.target}.json",
                SCRAPER_STATE_MIGRATIONS,
                check=check,
            )
        )

    def run(self, *, check: bool = False) -> tuple[MigrationOutcome, ...]:
        outcomes: list[MigrationOutcome] = []
        migration_lock = self.state_dir / ".migration.lock"
        with _held_lock(migration_lock):
            outcomes.append(
                self._run_one(
                    "general_config",
                    "general",
                    self.config_dir / "general.json",
                    GENERAL_CONFIG_MIGRATIONS,
                    check=check,
                )
            )
            for plugin in self.catalog.plugins:
                target_lock = (
                    self.logs_dir / plugin.target / f"{plugin.target}_scraper_running.lock"
                )
                try:
                    with _held_lock(target_lock):
                        self._run_target_locked(plugin, outcomes, check=check)
                except Exception as exc:
                    outcomes.extend(
                        (
                            self._failed_outcome(
                                "target_config",
                                plugin.target,
                                f"config/{plugin.config_filename}",
                                exc,
                            ),
                            self._failed_outcome(
                                "scraper_state",
                                plugin.target,
                                f"state/{plugin.target}.json",
                                exc,
                            ),
                        )
                    )
            reminder_lock = self.logs_dir / "reminder" / "reminder_check.lock"
            try:
                with _held_lock(reminder_lock):
                    outcomes.append(
                        self._run_one(
                            "reminder_state",
                            "general",
                            self.state_dir / "general.json",
                            REMINDER_STATE_MIGRATIONS,
                            check=check,
                        )
                    )
            except Exception as exc:
                outcomes.append(
                    self._failed_outcome(
                        "reminder_state",
                        "general",
                        "state/general.json",
                        exc,
                    )
                )

        failed = any(outcome.status == STATUS_FAILED for outcome in outcomes)
        if not failed and self._recovery is not None:
            shutil.rmtree(self._recovery)
            self._recovery = None
        return tuple(outcomes)


__all__ = [
    "MigrationOutcome",
    "MigrationRunner",
    "STATUS_CURRENT",
    "STATUS_FAILED",
    "STATUS_MIGRATED",
    "STATUS_MISSING",
]
