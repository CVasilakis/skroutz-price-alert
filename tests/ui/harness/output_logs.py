"""Deterministic capture of production-formatted background ``output.log`` files."""

from __future__ import annotations

import datetime
import logging
import os
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from core.infrastructure import locking as core_locking
from core.infrastructure import logging as core_logging
from ui.catalog._base import OutputLog

_FIXED_LOG_TIME = datetime.datetime(2026, 7, 4, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


class _FixedTimeFilter(logging.Filter):
    """Pin record creation time while leaving the production formatter untouched."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.created = _FIXED_LOG_TIME
        record.msecs = 0.0
        return True


class OutputLogCapture:
    """Own a temporary log tree and expose real quiet target loggers within it."""

    def __init__(self) -> None:
        self._temp_root = tempfile.mkdtemp(prefix="scrooge-ui-logs-")
        self.logs_dir = Path(self._temp_root) / "logs"
        self.locks_dir = Path(self._temp_root) / "state" / "locks"
        self._patches = (
            mock.patch.object(core_logging, "LOGS_DIR", str(self.logs_dir)),
            mock.patch.object(core_locking, "LOCKS_DIR", str(self.locks_dir)),
        )

    def __enter__(self) -> OutputLogCapture:
        for patcher in self._patches:
            patcher.start()
        return self

    def logger_for(
        self,
        target: str,
        quiet: bool = True,
        retention_days: int = core_logging.DEFAULT_LOG_RETENTION_DAYS,
    ) -> logging.Logger:
        """Return the real file logger with only its record clock made deterministic."""
        logger = core_logging.get_target_logger(target, quiet, retention_days)
        for handler in logger.handlers:
            if not any(isinstance(item, _FixedTimeFilter) for item in handler.filters):
                handler.addFilter(_FixedTimeFilter())
        return logger

    def artifacts(self) -> tuple[OutputLog, ...]:
        """Flush handlers and collect every output log by stable project-relative path."""
        self._flush_handlers()
        if not self.logs_dir.exists():
            return ()
        return tuple(
            OutputLog(
                path=os.path.join("logs", *path.relative_to(self.logs_dir).parts),
                content=path.read_text(encoding="utf-8"),
            )
            for path in sorted(self.logs_dir.rglob("output.log"))
        )

    def _flush_handlers(self) -> None:
        root = os.path.abspath(self.logs_dir)
        for candidate in logging.Logger.manager.loggerDict.values():
            if not isinstance(candidate, logging.Logger):
                continue
            for handler in candidate.handlers:
                filename = getattr(handler, "baseFilename", None)
                if filename and os.path.commonpath((root, os.path.abspath(filename))) == root:
                    handler.flush()

    def _close_handlers(self) -> None:
        root = os.path.abspath(self.logs_dir)
        for candidate in logging.Logger.manager.loggerDict.values():
            if not isinstance(candidate, logging.Logger):
                continue
            for handler in candidate.handlers[:]:
                filename = getattr(handler, "baseFilename", None)
                if filename and os.path.commonpath((root, os.path.abspath(filename))) == root:
                    handler.close()
                    candidate.removeHandler(handler)

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._close_handlers()
        for patcher in reversed(self._patches):
            patcher.stop()
        shutil.rmtree(self._temp_root, ignore_errors=True)


__all__ = ["OutputLogCapture"]
