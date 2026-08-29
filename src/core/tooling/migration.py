"""Cross-family migration inventory, validation, and safe persistence."""

from __future__ import annotations

import copy
import json
import os
import shutil
import stat
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.exceptions import LockAcquisitionError
from core.general.configuration import validate_general_migration_document
from core.general.migrations import GENERAL_CONFIG_MIGRATIONS, REMINDER_STATE_MIGRATIONS
from core.general.reminder_state import validate_reminder_state_document
from core.infrastructure.locking import StateLockManager
from core.infrastructure.persistence import (
    AtomicReplacementError,
    commit_atomic_replacement,
    fsync_directory,
)
from core.schema_migrations.contracts import JsonObject
from core.schema_migrations.engine import (
    MigrationError,
    MigrationPlan,
    document_version,
    migrate_document,
)
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import decode_target_document
from core.scrapers.framework.migrations import (
    SCRAPER_STATE_MIGRATIONS,
    TARGET_CONFIG_MIGRATIONS,
    PluginMigrationDeclarationError,
    load_plugin_config_migration_plan,
)
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.state import JsonStateRepository

STATUS_CURRENT = "current"
STATUS_MIGRATED = "migrated"
STATUS_MISSING = "missing"
STATUS_FAILED = "failed"


@dataclass(frozen=True)
class MigrationOutcome:
    """What happened to one managed document, reported whether or not it changed.

    Every document is reported, including the ones already current, so a run's
    output is a complete inventory rather than a list of changes — which is what
    makes ``--check`` usable as an audit.
    """

    family: str
    """Which independently versioned sequence this document belongs to."""

    target: str
    """The owning target, or the framework name for a shared document."""

    path: str
    """The document's path, for the contributor to inspect."""

    status: str
    """One of ``current``, ``migrated``, ``missing``, or ``failed``."""

    detail: str = ""
    """Why it failed, or which transitions ran; empty when unremarkable."""


@dataclass(frozen=True)
class _SourceSnapshot:
    data: bytes
    device: int
    inode: int
    mode: int


Validator = Callable[[JsonObject], object]


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
        commit_atomic_replacement(path, temporary)
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
def _held_lock(manager: StateLockManager, lock_name: str):
    path = manager.lock_path(lock_name)
    try:
        with manager.acquire(lock_name):
            yield
    except LockAcquisitionError as exc:
        raise MigrationError(f"another process holds lock {path}") from exc


class MigrationRunner:
    """Migrate all known documents with independent atomic commits."""

    def __init__(self, root: str | os.PathLike[str], catalog: PluginCatalog) -> None:
        self.root = Path(root)
        self.catalog = catalog
        self.config_dir = self.root / "config"
        self.state_dir = self.root / "state"
        self.lock_manager = StateLockManager(self.state_dir)
        self._recovery: Path | None = None

    @property
    def recovery_path(self) -> Path | None:
        """The directory holding pre-migration copies, once one has been created.

        Created lazily on first write and reported to the user, so a migration that
        fails part way through leaves the originals findable rather than only
        described.
        """
        return self._recovery

    def _ensure_recovery(self) -> Path:
        if self._recovery is None:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            fsync_directory(self.state_dir)
            fsync_directory(self.state_dir.parent)
            self._recovery = Path(
                tempfile.mkdtemp(prefix=".migration-recovery.", dir=self.state_dir)
            )
            self._recovery.chmod(0o700)
            fsync_directory(self._recovery)
            fsync_directory(self.state_dir)
        return self._recovery

    def _backup(self, path: Path, snapshot: _SourceSnapshot) -> None:
        relative = path.relative_to(self.root)
        backup = self._ensure_recovery() / relative
        backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fsync_directory(backup.parent)
        fsync_directory(backup.parent.parent)
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, snapshot.mode)
        os.fchmod(descriptor, snapshot.mode)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(snapshot.data)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(backup.parent)

    def _run_one(
        self,
        family: str,
        target: str,
        path: Path,
        plans: tuple[MigrationPlan, ...],
        validator: Validator,
        *,
        check: bool,
    ) -> MigrationOutcome:
        shown = str(path.relative_to(self.root))
        try:
            if not path.exists() and not path.is_symlink():
                return MigrationOutcome(family, target, shown, STATUS_MISSING)
            document, snapshot = _read_document(path)
            original_versions = tuple(
                document_version(document, plan.version_key) for plan in plans
            )
            migrated = self._migrate_plans(document, plans, validator)
            changed = tuple(
                (plan, original)
                for plan, original in zip(plans, original_versions, strict=True)
                if original != plan.current_version
            )
            if not changed:
                return MigrationOutcome(family, target, shown, STATUS_CURRENT)
            version_detail = self._version_detail(plans, original_versions)
            rendered = _serialize(migrated)
            if check:
                return MigrationOutcome(
                    family,
                    target,
                    shown,
                    STATUS_MIGRATED,
                    f"pending {version_detail}",
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
                version_detail,
            )
        except Exception as exc:
            return self._failed_outcome(
                family,
                target,
                shown,
                exc,
                original_preserved=not (
                    isinstance(exc, AtomicReplacementError) and exc.destination_replaced
                ),
            )

    @staticmethod
    def _migrate_plans(
        document: JsonObject,
        plans: tuple[MigrationPlan, ...],
        validator: Validator,
    ) -> JsonObject:
        if not plans:
            raise ValueError("at least one migration plan is required")
        keys = tuple(plan.version_key for plan in plans)
        if len(set(keys)) != len(keys):
            raise ValueError("migration plans must own distinct version keys")
        migrated = copy.deepcopy(document)
        for plan in plans:
            protected = {
                key: document_version(migrated, key) for key in keys if key != plan.version_key
            }
            migrated = migrate_document(migrated, plan)
            for key, before in protected.items():
                if document_version(migrated, key) != before:
                    raise MigrationError(f"{plan.version_key} migration must not change {key}")
        try:
            validator(copy.deepcopy(migrated))
        except Exception as exc:
            versions = ", ".join(f"{plan.version_key} v{plan.current_version}" for plan in plans)
            raise MigrationError(f"current-schema validation at {versions} failed: {exc}") from exc
        return migrated

    @staticmethod
    def _version_detail(
        plans: tuple[MigrationPlan, ...],
        original_versions: tuple[int, ...],
    ) -> str:
        changed = [
            (plan, original)
            for plan, original in zip(plans, original_versions, strict=True)
            if original != plan.current_version
        ]
        if len(plans) == 1:
            plan, original = changed[0]
            return f"v{original} to v{plan.current_version}"
        labels = {
            "schema_version": "framework",
            "plugin_schema_version": "plugin",
        }
        return "; ".join(
            f"{labels.get(plan.version_key, plan.version_key)} "
            f"v{original} to v{plan.current_version}"
            for plan, original in changed
        )

    @staticmethod
    def _failed_outcome(
        family: str,
        target: str,
        shown: str,
        exc: Exception,
        *,
        original_preserved: bool = True,
    ) -> MigrationOutcome:
        if not original_preserved:
            advice = (
                "A durable recovery copy was retained; the destination may already "
                "contain the migrated bytes, so inspect both before retrying."
            )
        elif family == "target_config":
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
                try:
                    plugin_plan = load_plugin_config_migration_plan(plugin)
                except PluginMigrationDeclarationError as exc:
                    raise MigrationError(str(exc)) from exc

                def validate_target(document: dict[str, Any]) -> None:
                    decode_target_document(plugin, document)

                outcomes.append(
                    self._run_one(
                        "target_config",
                        plugin.target,
                        config_path,
                        (TARGET_CONFIG_MIGRATIONS, plugin_plan),
                        validate_target,
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
                (SCRAPER_STATE_MIGRATIONS,),
                JsonStateRepository.validate_document,
                check=check,
            )
        )

    def run(self, *, check: bool = False) -> tuple[MigrationOutcome, ...]:
        """Migrate, or in check mode validate, every managed document under one lock.

        The whole sweep holds the migration lock so no run can start mid-migration
        and read a document that is between versions. Each document is transformed
        entirely in memory and committed with one atomic replacement, so a document
        is either fully at the old version or fully at the new one.

        Args:
            check: Validate and report without modifying anything. The lock is
                still taken, so check mode may create ``state/locks/``.

        Returns:
            One outcome per managed document, in a stable order.
        """
        outcomes: list[MigrationOutcome] = []
        with _held_lock(self.lock_manager, "migration"):
            outcomes.append(
                self._run_one(
                    "general_config",
                    "general",
                    self.config_dir / "general.json",
                    (GENERAL_CONFIG_MIGRATIONS,),
                    validate_general_migration_document,
                    check=check,
                )
            )
            for plugin in self.catalog.plugins:
                try:
                    with _held_lock(self.lock_manager, plugin.target):
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
            try:
                with _held_lock(self.lock_manager, "reminder"):
                    outcomes.append(
                        self._run_one(
                            "reminder_state",
                            "general",
                            self.state_dir / "general.json",
                            (REMINDER_STATE_MIGRATIONS,),
                            validate_reminder_state_document,
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
