import errno
import io
import os
import pty
import signal
import termios
from unittest import mock

import pytest

from core.scrapers.tooling.scaffold import terminal
from core.scrapers.tooling.scaffold.terminal import (
    REFRESH,
    InteractiveTerminalUnavailable,
    ScaffoldInterrupted,
    TerminalStateError,
    interruption_guard,
    read_terminal_key,
    terminal_reader,
)


def _terminal_streams():
    master, slave = pty.openpty()
    stdin = os.fdopen(os.dup(slave), "r", encoding="utf-8", buffering=1)
    stdout = os.fdopen(os.dup(slave), "w", encoding="utf-8", buffering=1)
    return master, slave, stdin, stdout


@pytest.mark.parametrize("failure", [RuntimeError("boom"), KeyboardInterrupt()])
def test_terminal_reader_restores_exact_settings_after_failures(failure):
    master, slave, stdin, stdout = _terminal_streams()
    original = termios.tcgetattr(slave)
    try:
        with pytest.raises(type(failure)):
            with terminal_reader(stdin, stdout):
                changed = termios.tcgetattr(slave)
                assert changed != original
                assert changed[3] & termios.ICANON == 0
                raise failure
        assert termios.tcgetattr(slave) == original
    finally:
        stdin.close()
        stdout.close()
        os.close(master)
        os.close(slave)


def test_terminal_reader_restores_exact_settings_after_normal_completion():
    master, slave, stdin, stdout = _terminal_streams()
    original = termios.tcgetattr(slave)
    try:
        with terminal_reader(stdin, stdout):
            assert termios.tcgetattr(slave) != original
        assert termios.tcgetattr(slave) == original
    finally:
        stdin.close()
        stdout.close()
        os.close(master)
        os.close(slave)


def test_terminal_reader_restores_after_partial_cbreak_failure():
    master, slave, stdin, stdout = _terminal_streams()
    original = termios.tcgetattr(slave)
    real_setcbreak = terminal.tty.setcbreak

    def fail_after_change(descriptor: int) -> None:
        real_setcbreak(descriptor)
        raise termios.error(errno.EIO, "injected failure")

    try:
        with mock.patch.object(terminal.tty, "setcbreak", side_effect=fail_after_change):
            with pytest.raises(TerminalStateError, match="could not enter"):
                with terminal_reader(stdin, stdout):
                    pass
        assert termios.tcgetattr(slave) == original
    finally:
        stdin.close()
        stdout.close()
        os.close(master)
        os.close(slave)


def test_terminal_reader_rejects_non_tty_streams_without_mutation():
    with pytest.raises(InteractiveTerminalUnavailable, match="interactive terminal"):
        with terminal_reader(io.StringIO(), io.StringIO()):
            pass


def test_terminal_reader_restores_handlers_and_reports_restore_failure():
    master, slave, stdin, stdout = _terminal_streams()
    previous_tstp = signal.getsignal(signal.SIGTSTP)
    previous_cont = signal.getsignal(signal.SIGCONT)
    try:
        with mock.patch.object(
            terminal._TerminalSession,
            "_restore",
            side_effect=TerminalStateError("could not restore terminal settings"),
        ):
            with pytest.raises(TerminalStateError, match="could not restore"):
                with terminal_reader(stdin, stdout):
                    pass
        assert signal.getsignal(signal.SIGTSTP) is previous_tstp
        assert signal.getsignal(signal.SIGCONT) is previous_cont
    finally:
        stdin.close()
        stdout.close()
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize(
    "signum",
    [
        getattr(signal, name)
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
        if hasattr(signal, name)
    ],
)
def test_interruption_guard_raises_typed_interrupt_and_restores_handlers(signum):
    previous = signal.getsignal(signum)

    with pytest.raises(ScaffoldInterrupted) as raised:
        with interruption_guard():
            handler = signal.getsignal(signum)
            assert callable(handler)
            handler(signum, None)

    assert raised.value.signum == signum
    assert signal.getsignal(signum) is previous


def test_terminal_restore_retries_one_interrupted_system_call():
    session = terminal._TerminalSession(7)
    session._original = [object()]
    session._restore_required = True
    with mock.patch(
        "core.scrapers.tooling.scaffold.terminal.termios.tcsetattr",
        side_effect=(InterruptedError(errno.EINTR, "interrupted"), None),
    ) as restore:
        session._restore()

    assert restore.call_count == 2
    assert not session._restore_required


def test_terminal_session_restores_before_suspend_and_refreshes_after_continue():
    session = terminal._TerminalSession(7)
    session._original = [object()]
    session._active = True
    events: list[str] = []

    def restore() -> None:
        events.append("restore")
        session._active = False

    def enable() -> None:
        events.append("enable")
        session._active = True

    with (
        mock.patch.object(session, "_restore", side_effect=restore),
        mock.patch.object(session, "_enable", side_effect=enable),
        mock.patch("core.scrapers.tooling.scaffold.terminal.signal.signal"),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.kill",
            side_effect=lambda _pid, _signal: events.append("stop"),
        ),
    ):
        session._suspend(signal.SIGTSTP, None)

    assert events == ["restore", "stop", "enable"]
    assert session.read_key() == REFRESH


def test_terminal_key_reader_treats_closed_input_as_eof():
    with mock.patch("core.scrapers.tooling.scaffold.terminal.os.read", return_value=b""):
        with pytest.raises(EOFError, match="terminal input closed"):
            read_terminal_key(7)


def test_terminal_key_reader_treats_incomplete_utf8_as_eof():
    with (
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.read",
            side_effect=(b"\xce", b""),
        ),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([object()], [], []),
        ),
    ):
        with pytest.raises(EOFError, match="terminal input closed"):
            read_terminal_key(7)


def test_terminal_key_reader_bounds_incomplete_utf8_wait():
    with (
        mock.patch("core.scrapers.tooling.scaffold.terminal.os.read", return_value=b"\xce"),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([], [], []),
        ),
    ):
        assert read_terminal_key(7) == ""
