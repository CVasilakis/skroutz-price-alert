"""Process-signal naming and one-shot entry-point interruption handling."""

from __future__ import annotations

import os
import signal
import sys

from core.constants import EXIT_CODE_INTERRUPT


def describe_signal(signum: int) -> str:
    """Return a human-readable name for a termination signal."""
    if signum == signal.SIGINT:
        return "SIGINT (Ctrl+C)"
    if signum == signal.SIGTERM:
        return "SIGTERM (System Shutdown/Termination)"
    return str(signum)


def install_interrupt_handler() -> None:
    """Install SIGINT/SIGTERM handlers that print a clean message and exit."""
    # Deferred so importing this module does not load Rich for non-rendering paths.
    from rich.console import Console

    def _handler(signum: int, _frame: object) -> None:
        os.write(1, b"\033[2K\r")
        Console().print(f"🛑 Interrupted! Received signal {describe_signal(signum)}.\n")
        sys.exit(EXIT_CODE_INTERRUPT)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)


__all__ = ["describe_signal", "install_interrupt_handler"]
