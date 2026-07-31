import os
import stat
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from core import messages
from core.constants import LOCK_TIMEOUT
from core.exceptions import LockAcquisitionError, LockStorageError
from core.infrastructure.persistence import storage_diagnostic


def _validate_lock_name(lock_name: str) -> None:
    if (
        not isinstance(lock_name, str)
        or not lock_name
        or Path(lock_name).name != lock_name
        or any(ord(character) < 32 or ord(character) == 127 for character in lock_name)
    ):
        raise ValueError("lock name must be a nonblank filename without control characters")


class StateLockManager:
    """Own cooperative locks colocated with one machine-state root."""

    def __init__(self, state_dir: str | os.PathLike[str]) -> None:
        self.state_dir = Path(state_dir)
        self.locks_dir = self.state_dir / "locks"

    def lock_path(self, lock_name: str) -> Path:
        """Return the canonical path for one target or framework lock."""
        _validate_lock_name(lock_name)
        return self.locks_dir / f"{lock_name}.lock"

    def _prepare_lock_path(self, lock_name: str) -> Path:
        path = self.lock_path(lock_name)
        for candidate in (self.state_dir, self.locks_dir):
            if candidate.is_symlink():
                raise OSError(f"managed lock directory must not be a symlink: {candidate}")
            candidate.mkdir(parents=True, exist_ok=True)
            if candidate.is_symlink() or not candidate.is_dir():
                raise OSError(f"managed lock directory must be a real directory: {candidate}")

        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            return path
        if not stat.S_ISREG(mode):
            raise OSError(f"managed lock path must be a regular file: {path}")
        return path

    @contextmanager
    def acquire(self, lock_name: str):
        """Acquire one exclusive project lock without waiting."""
        path = self.lock_path(lock_name)
        try:
            lock = FileLock(self._prepare_lock_path(lock_name), timeout=LOCK_TIMEOUT)
            lock.acquire()
        except Timeout:
            raise LockAcquisitionError from None
        except OSError as exc:
            raise LockStorageError(
                messages.lock_storage_unavailable(lock_name),
                storage_diagnostic(path, exc, operation="acquire machine-state lock"),
            ) from exc
        try:
            yield
        finally:
            try:
                lock.release()
            except OSError as exc:
                raise LockStorageError(
                    messages.lock_storage_unavailable(lock_name),
                    storage_diagnostic(path, exc, operation="release machine-state lock"),
                ) from exc


__all__ = ["StateLockManager"]
