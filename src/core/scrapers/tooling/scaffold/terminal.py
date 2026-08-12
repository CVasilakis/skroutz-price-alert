"""Private terminal and signal lifecycle for the plugin scaffold wizard."""

from __future__ import annotations

import errno
import os
import select
import signal
import sys
import termios
import time
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
_SEQUENCE_TIMEOUT = 0.2


class _SequenceTimeout(Exception):
    """A partial terminal sequence did not complete within its bounded window."""


class ScaffoldInterrupted(BaseException):
    """A catchable process signal that must unwind scaffold cleanup first."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class InteractiveTerminalUnavailable(RuntimeError):
    """The wizard cannot start because its streams are not interactive."""


class UnsupportedTerminalError(InteractiveTerminalUnavailable):
    """The wizard cannot safely render its transient interface on this terminal."""


class TerminalStateError(RuntimeError):
    """The wizard could not safely change or restore terminal state."""


def _handled_interrupt_signals() -> tuple[signal.Signals, ...]:
    names = ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


def _handled_job_signals() -> tuple[signal.Signals, ...]:
    names = ("SIGTSTP", "SIGCONT")
    return tuple(getattr(signal, name) for name in names if hasattr(signal, name))


@contextmanager
def _blocked_signals(signals: tuple[signal.Signals, ...]) -> Iterator[None]:
    """Defer lifecycle signals across short terminal transition windows."""
    if not signals or not hasattr(signal, "pthread_sigmask"):
        yield
        return
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


@contextmanager
def interruption_guard() -> Iterator[None]:
    """Turn catchable termination signals into stack-unwinding interruptions."""
    previous = {signum: signal.getsignal(signum) for signum in _handled_interrupt_signals()}

    def interrupt(signum: int, _frame: FrameType | None) -> None:
        raise ScaffoldInterrupted(signum)

    installed: list[signal.Signals] = []
    try:
        with _blocked_signals(tuple(previous)):
            for signum in previous:
                signal.signal(signum, interrupt)
                installed.append(signum)
        yield
    finally:
        with _blocked_signals(tuple(previous)):
            for signum in installed:
                signal.signal(signum, previous[signum])


def _wait_for_byte(descriptor: int, deadline: float) -> None:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _SequenceTimeout
        try:
            ready = select.select([descriptor], [], [], remaining)[0]
        except InterruptedError:
            continue
        if not ready:
            raise _SequenceTimeout
        return


def _read_byte(descriptor: int, deadline: float | None = None) -> bytes:
    if deadline is not None:
        _wait_for_byte(descriptor, deadline)
    value = os.read(descriptor, 1)
    if not value:
        raise EOFError("terminal input closed")
    return value


def _read_character(descriptor: int, deadline: float | None = None) -> str:
    first = _read_byte(descriptor, deadline)
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
    continuation_deadline = deadline or (time.monotonic() + _SEQUENCE_TIMEOUT)
    try:
        encoded = first + b"".join(
            _read_byte(descriptor, continuation_deadline) for _ in range(remaining)
        )
    except _SequenceTimeout:
        return ""
    try:
        return encoded.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def _read_escape_sequence(descriptor: int) -> str:
    deadline = time.monotonic() + _SEQUENCE_TIMEOUT
    try:
        second = _read_character(descriptor, deadline)
    except _SequenceTimeout:
        return ABORT
    if second not in {"[", "O"}:
        return ""
    sequence = ""
    for _ in range(8):
        try:
            character = _read_character(descriptor, deadline)
        except _SequenceTimeout:
            return ""
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
        self._restore_required = False
        self._resumed = False
        self._previous_job_handlers: dict[signal.Signals, Any] = {}

    def _enable(self) -> None:
        if self._active:
            return
        self._restore_required = True
        try:
            tty.setcbreak(self._descriptor)
        except (OSError, termios.error) as exc:
            raise TerminalStateError(f"could not enter terminal cbreak mode: {exc}") from exc
        self._active = True

    def _restore(self) -> None:
        if (not self._active and not self._restore_required) or self._original is None:
            return
        for attempt in range(2):
            try:
                termios.tcsetattr(self._descriptor, termios.TCSANOW, self._original)
                break
            except (OSError, termios.error) as exc:
                error_number = getattr(exc, "errno", None)
                if error_number is None and exc.args and isinstance(exc.args[0], int):
                    error_number = exc.args[0]
                if error_number == errno.EINTR and attempt == 0:
                    continue
                raise TerminalStateError(f"could not restore terminal settings: {exc}") from exc
        self._active = False
        self._restore_required = False

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
        transition_signals = _handled_interrupt_signals() + _handled_job_signals()
        try:
            with _blocked_signals(transition_signals):
                for signum, handler in (
                    (getattr(signal, "SIGTSTP", None), self._suspend),
                    (getattr(signal, "SIGCONT", None), self._continued),
                ):
                    if signum is not None:
                        self._previous_job_handlers[signum] = signal.getsignal(signum)
                        signal.signal(signum, handler)
                self._enable()
        except BaseException:
            with _blocked_signals(transition_signals):
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
        transition_signals = _handled_interrupt_signals() + _handled_job_signals()
        with _blocked_signals(transition_signals):
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
    terminal_type = os.environ.get("TERM", "").strip().lower()
    if not terminal_type:
        raise UnsupportedTerminalError(
            "the guided wizard requires TERM to identify a terminal with ANSI cursor support"
        )
    if terminal_type in {"dumb", "unknown"}:
        raise UnsupportedTerminalError(
            f"the guided wizard does not support TERM={terminal_type}; "
            "use a terminal with ANSI cursor support"
        )
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
    "UnsupportedTerminalError",
    "interruption_guard",
    "read_terminal_key",
    "terminal_reader",
]
