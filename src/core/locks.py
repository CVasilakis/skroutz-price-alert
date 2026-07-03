import os
from contextlib import contextmanager
from filelock import FileLock, Timeout
from core.constants import LOGS_DIR, LOCK_TIMEOUT
from core.exceptions import LockAcquisitionError

@contextmanager
def acquire_lock(target_name: str):
    """Attempts to acquire an exclusive execution lock for the given target.

    Args:
        target_name (str): The identifier for the scraper (e.g., 'skroutz').

    Yields:
        None: If the lock is successfully acquired.

    Raises:
        LockAcquisitionError: If the target is currently locked by another process.
    """
    target_dir = os.path.join(LOGS_DIR, target_name)
    os.makedirs(target_dir, exist_ok=True)
    lock_filename = f"{target_name}_scraper_running.lock"
    lock_path = os.path.join(target_dir, lock_filename)
    lock = FileLock(lock_path, timeout=LOCK_TIMEOUT)

    try:
        with lock:
            yield
    except Timeout:
        raise LockAcquisitionError
