"""Private terminal and signal lifecycle for the plugin scaffold wizard."""

from __future__ import annotations

import os
import select
import signal
import sys
import termios
import tty
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType
from typing import Any, TextIO

ABORT = "abort"
BACK = "back"
ACCEPT = "accept"
LEFT = "left"
RIGHT = "right"
HOME = "home"
END = "end"
DELETE = "delete"
BACKSPACE = "backspace"
REFRESH = "refresh"

KeyReader = Callable[[], str]
SignalHandler = Callable[[int, FrameType | None], None]


class ScaffoldInterrupted(BaseException):
    """A catchable process signal that must unwind scaffold cleanup first."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class InteractiveTerminalUnavailable(RuntimeError):
    """The wizard cannot start because its streams are not interactive."""


class TerminalStateError(RuntimeError):
    """The wizard could not safely change or restore terminal state."""


def _handled_interrupt_signals() -> tuple[signal.Signals, ...]:
    names = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


@contextmanager
def interruption_guard() -> Iterator[None]:
    """Turn catchable termination signals into stack-unwinding interruptions."""
    previous = {signum: signal.getsignal(signum) for signum in _handled_interrupt_signals()}

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        raise ScaffoldInterrupted(signum)

    try:
        for signum in previous:
            signal.signal(signum, interrupt)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _read_byte(descriptor: int) -> bytes:
    value = os.read(descriptor, 1)
    if not value:
        raise EOFError("terminal input closed")
    return value


def _read_character(descriptor: int) -> str:
    first = _read_byte(descriptor)
    leading = first[0]
    if leading < 0x80:
        return first.decode()
    if leading & 0xE0 == 0xC0:
        remaining = 1
    elif leading & 0xF0 == 0xE0:
        remaining = 2
    elif leading & 0xF8 == 0xF0:
        remaining = 3
    else:
        return ""
    encoded = first + b"".join(_read_byte(descriptor) for _ in range(remaining))
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _read_escape_sequence(descriptor: int) -> str:
    if not select.select([descriptor], [], [], 0.2)[0]:
        return ABORT
    second = _read_character(descriptor)
    if second not in {"[", "O"}:
        return ""
    sequence = ""
    for _ in range(8):
        if not select.select([descriptor], [], [], 0.2)[0]:
            return ""
        character = _read_character(descriptor)
        sequence += character
        if character.isalpha() or character == "~":
            break
    return {
        "A": BACK,
        "B": ACCEPT,
        "C": RIGHT,
        "D": LEFT,
        "H": HOME,
        "F": END,
        "3~": DELETE,
    }.get(sequence, "")


def read_terminal_key(descriptor: int | None = None) -> str:
    """Read one printable character or normalized navigation action."""
    resolved_descriptor = sys.stdin.fileno() if descriptor is None else descriptor
    character = _read_character(resolved_descriptor)
    if character == "\x1b":
        return _read_escape_sequence(resolved_descriptor)
    if character in {"\r", "\n"}:
        return ACCEPT
    if character in {"\x7f", "\b"}:
        return BACKSPACE
    if character == "\x03":
        raise ScaffoldInterrupted(signal.SIGINT)
    if character == "\x04":
        return ABORT
    return character if character.isprintable() else ""


class _TerminalSession:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor
        self._original: Any | None = None
        self._active = False
        self._resumed = False
        self._previous_job_handlers: dict[signal.Signals, Any] = {}

    def _enable(self) -> None:
        if self._active:
            return
        try:
            tty.setcbreak(self._descriptor)
        except (OSError, termios.error) as exc:
            raise TerminalStateError(f"could not enter terminal cbreak mode: {exc}") from exc
        self._active = True

    def _restore(self) -> None:
        if not self._active or self._original is None:
            return
        try:
            termios.tcsetattr(self._descriptor, termios.TCSANOW, self._original)
        except (OSError, termios.error) as exc:
            raise TerminalStateError(f"could not restore terminal settings: {exc}") from exc
        self._active = False

    def _continued(self, _signum: int, _frame: FrameType | None) -> None:
        self._enable()
        self._resumed = True

    def _suspend(self, _signum: int, _frame: FrameType | None) -> None:
        self._restore()
        signal.signal(signal.SIGTSTP, signal.SIG_DFL)
        try:
            os.kill(os.getpid(), signal.SIGTSTP)
        finally:
            signal.signal(signal.SIGTSTP, self._suspend)
        # An orphaned process group may ignore SIGTSTP. If execution was not
        # actually suspended and resumed through SIGCONT, do not leave the
        # still-running wizard in canonical mode.
        if not self._active:
            self._continued(signal.SIGCONT, None)

    def __enter__(self) -> _TerminalSession:
        try:
            self._original = termios.tcgetattr(self._descriptor)
        except (OSError, termios.error) as exc:
            raise TerminalStateError(f"could not read terminal settings: {exc}") from exc
        try:
            for signum, handler in (
                (getattr(signal, "SIGTSTP", None), self._suspend),
                (getattr(signal, "SIGCONT", None), self._continued),
            ):
                if signum is not None:
                    self._previous_job_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, handler)
            self._enable()
        except BaseException:
            try:
                self._restore()
            finally:
                self._restore_job_handlers()
            raise
        return self

    def _restore_job_handlers(self) -> None:
        for signum, handler in self._previous_job_handlers.items():
            signal.signal(signum, handler)
        self._previous_job_handlers.clear()

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        try:
            self._restore()
        finally:
            self._restore_job_handlers()

    def read_key(self) -> str:
        if self._resumed:
            self._resumed = False
            return REFRESH
        return read_terminal_key(self._descriptor)


@contextmanager
def terminal_reader(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> Iterator[KeyReader]:
    """Yield a key reader while preserving the user's exact terminal settings."""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    if not input_stream.isatty() or not output_stream.isatty():
        raise InteractiveTerminalUnavailable("the guided wizard requires an interactive terminal")
    with _TerminalSession(input_stream.fileno()) as session:
        yield session.read_key


__all__ = [
    "ABORT",
    "ACCEPT",
    "BACK",
    "BACKSPACE",
    "DELETE",
    "END",
    "HOME",
    "InteractiveTerminalUnavailable",
    "KeyReader",
    "LEFT",
    "REFRESH",
    "RIGHT",
    "ScaffoldInterrupted",
    "TerminalStateError",
    "interruption_guard",
    "read_terminal_key",
    "terminal_reader",
]
