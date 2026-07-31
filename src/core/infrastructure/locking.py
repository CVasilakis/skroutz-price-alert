import os
import stat
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from core.constants import LOCK_TIMEOUT, LOCKS_DIR
from core.exceptions import LockAcquisitionError


def managed_lock_path(locks_dir: str | os.PathLike[str], lock_name: str) -> Path:
    """Prepare and return one regular-file lock destination below ``locks_dir``."""
    if (
        not isinstance(lock_name, str)
        or not lock_name
        or Path(lock_name).name != lock_name
        or any(ord(character) < 32 or ord(character) == 127 for character in lock_name)
    ):
        raise ValueError("lock name must be a nonblank filename without control characters")

    directory = Path(locks_dir)
    for candidate in (directory.parent, directory):
        if candidate.is_symlink():
            raise OSError(f"managed lock directory must not be a symlink: {candidate}")
        candidate.mkdir(parents=True, exist_ok=True)
        if candidate.is_symlink() or not candidate.is_dir():
            raise OSError(f"managed lock directory must be a real directory: {candidate}")

    path = directory / f"{lock_name}.lock"
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return path
    if not stat.S_ISREG(mode):
        raise OSError(f"managed lock path must be a regular file: {path}")
    return path


@contextmanager
def acquire_lock(lock_name: str):
    """Attempt to acquire one exclusive project lock without waiting.

    Args:
        lock_name: A scraper target or reserved framework lock name. The lock lives at
            ``state/locks/<lock_name>.lock``.

    Yields:
        None: If the lock is successfully acquired.

    Raises:
        LockAcquisitionError: If the target is currently locked by another process.
    """
    lock_path = managed_lock_path(LOCKS_DIR, lock_name)
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)

    try:
        with lock:
            yield
    except Timeout:
        raise LockAcquisitionError


__all__ = ["acquire_lock", "managed_lock_path"]
