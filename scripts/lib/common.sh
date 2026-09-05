#!/bin/sh
# Shared POSIX-shell foundations. Callers define BASE_DIR before sourcing.

# shellcheck disable=SC2034  # colors and paths are consumed by sourcing scripts
if [ -z "${NO_COLOR:-}" ] && { [ -t 1 ] || [ -n "${CLICOLOR_FORCE:-}" ]; }; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    COMMAND_COLOR='\033[1;36m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    COMMAND_COLOR=''
    NC=''
fi

SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# Cross-command statuses consumed by the POSIX-shell management layer. The Python
# producer uses core.exit_status.ExitStatus; tests pin this small protocol boundary.
EXIT_STATUS_TARGET_CONFIG_ERROR=15
EXIT_STATUS_NOTIFICATION_CONFIG_ERROR=16
EXIT_STATUS_STORAGE_ERROR=19

# DEBUG_MODE is process-local public state. SCROOGE_INTERNAL_DEBUG is only for
# propagating that state between project scripts; invalid inherited values fail
# closed to normal (quiet) execution.
case "${SCROOGE_INTERNAL_DEBUG:-0}" in
    1) DEBUG_MODE=1 ;;
    *) DEBUG_MODE=0 ;;
esac
export DEBUG_MODE

begin_operational_output() {
    printf '\n'
}

section_heading() {
    case "$1" in
        success) _sh_marker='+'; _sh_color="$GREEN" ;;
        warning) _sh_marker='!'; _sh_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    shift
    printf '%b[%s]%b %s\n' "$_sh_color" "$_sh_marker" "$NC" "$*"
}

task_status() {
    case "$1" in
        success) _ts_marker='v'; _ts_color="$GREEN" ;;
        failure) _ts_marker='x'; _ts_color="$RED" ;;
        info) _ts_marker='i'; _ts_color="$CYAN" ;;
        warning) _ts_marker='!'; _ts_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    shift
    _ts_prefix="    ${_ts_color}[${_ts_marker}]${NC} "
    _print_indented_wrapped "$_ts_prefix" '        ' "$@"
}

# Render an actionable command distinctly without adding quotes that could be
# mistaken for literal shell syntax. Callers embed the result in task prose.
command_text() {
    printf '%b' "$COMMAND_COLOR"
    _ct_separator=''
    for _ct_part in "$@"; do
        printf '%s%s' "$_ct_separator" "$_ct_part"
        _ct_separator=' '
    done
    printf '%b' "$NC"
}

# Deliberately isolated so shell tests can synchronize the delayed presentation
# without depending on wall-clock timing.
progress_delay() {
    sleep 1
}

# One limit for both bounded waits below (the mkdir mutex and the presenter
# reap). They are deliberately not separate policies: each waits on the other
# half of the same two-process protocol, each peer only ever occupies the awaited
# state for a few statements, and each exhaustion degrades the same way, by
# abandoning the progress line rather than the command. The unit that matters is
# time, not tries: the wait between attempts is a yield rather than a delay, so
# twenty of them is on the order of twenty milliseconds — ample for a peer that
# is merely descheduled, and too short for a wedged one to hold up a user-facing
# command. tests/shell/test_common_sh.py pins the count from the outside.
PROGRESS_MAX_ATTEMPTS=20

# Delayed progress runs two processes: the parent (run_with_progress, which keeps
# the real command in the foreground) and a background presenter
# (_progress_present_after_delay). They coordinate entirely through the entries of
# one private mktemp workspace directory:
#
#   active     Regular file. Present while the progress line is still wanted. The
#              parent creates it before forking the presenter and removes it once
#              the command returns; the presenter refuses to print without it.
#   lock       Directory used as a mkdir mutex. Every read or write of the other
#              three entries happens while holding it, so the presenter's
#              "still wanted, so print" and the parent's "finished, so revoke"
#              can never interleave.
#   delay-pid  PID of the backgrounded progress_delay, published by the presenter
#              so a parent whose command finished early can end the wait directly
#              instead of leaving a stray sleep behind.
#   shown      Created by the presenter only after task_status actually wrote the
#              line. It is the sole positive evidence that there is something on
#              the terminal to erase.
#
# The parent erases only when the presenter has been reaped, the parent revoked
# `active` while holding the lock, and `shown` exists. Each condition rules out a
# different way a blind cursor-up would destroy unrelated output: a live presenter
# could still print after the erase, an unrevoked (or unobserved) `active` means a
# line may still be coming, and a missing `shown` means the row above belongs to
# the caller. Every failure therefore degrades to leaving the progress line in
# place, which is cosmetic, rather than erasing the wrong row.
#
# The single-row assumption behind that one cursor-up is established by
# _progress_capabilities, which refuses the whole mechanism unless the rendered
# line is strictly narrower than the terminal, so it cannot soft-wrap.

_progress_capabilities() {
    _pc_message="$1"
    PROGRESS_CURSOR_UP=''
    PROGRESS_CARRIAGE_RETURN=''
    PROGRESS_ERASE_LINE=''
    PROGRESS_COLUMNS=''

    [ "$DEBUG_MODE" -eq 0 ] || return 1
    [ -t 1 ] || return 1
    [ -z "${CI:-}" ] || return 1
    case "${TERM:-}" in
        ''|dumb) return 1 ;;
    esac
    command -v tput >/dev/null 2>&1 || return 1

    PROGRESS_CURSOR_UP="$(tput cuu1 2>/dev/null)" || return 1
    PROGRESS_CARRIAGE_RETURN="$(tput cr 2>/dev/null)" || return 1
    PROGRESS_ERASE_LINE="$(tput el 2>/dev/null)" || return 1
    PROGRESS_COLUMNS="$(tput cols 2>/dev/null)" || return 1
    case "$PROGRESS_COLUMNS" in
        ''|*[!0-9]*) return 1 ;;
    esac
    [ -n "$PROGRESS_CURSOR_UP" ] || return 1
    [ -n "$PROGRESS_CARRIAGE_RETURN" ] || return 1
    [ -n "$PROGRESS_ERASE_LINE" ] || return 1

    _pc_line="    [i] $_pc_message"
    [ "${#_pc_line}" -lt "$PROGRESS_COLUMNS" ]
}

# Bounded mkdir mutex over the workspace. A lost race is retried because a holder
# only ever keeps the lock for a few statements, but a missing workspace or an
# mkdir failure with no lock directory to blame is a real filesystem problem and
# fails immediately instead of spinning. Failure never means "the lock is yours":
# both sides treat it as "the other side may still act".
_progress_lock() {
    _pl_workspace="$1"
    _pl_attempt=0
    while [ "$_pl_attempt" -lt "$PROGRESS_MAX_ATTEMPTS" ]; do
        [ -d "$_pl_workspace" ] || return 1
        if mkdir "$_pl_workspace/lock" 2>/dev/null; then
            return 0
        fi
        [ -d "$_pl_workspace/lock" ] || return 1
        _pl_attempt=$((_pl_attempt + 1))
        [ "$_pl_attempt" -lt "$PROGRESS_MAX_ATTEMPTS" ] || break
        # `sleep` is an external command, so `sleep 0` yields the CPU to the lock
        # holder without spending wall-clock time. A non-zero status means either
        # that the fork failed or that a signal reached this shell; retrying is
        # futile in the first case and unwanted in the second, since callers run
        # this under HUP/INT/TERM traps and must not spin out the remaining
        # attempts before the handler gets to run.
        sleep 0 || return 1
    done
    return 1
}

# Terminate the presenter and reap it. Returns 0 only when the process is
# confirmed gone. After the attempt limit it escalates to SIGKILL and returns 1
# without reaping, which tells the parent the presenter may still write: neither
# the erase nor the workspace removal may proceed on that path.
_progress_stop_process() {
    _psp_pid="$1"
    case "$_psp_pid" in
        ''|*[!0-9]*) return 0 ;;
    esac

    kill "$_psp_pid" 2>/dev/null || true
    _psp_attempt=0
    while kill -0 "$_psp_pid" 2>/dev/null; do
        _psp_attempt=$((_psp_attempt + 1))
        if [ "$_psp_attempt" -ge "$PROGRESS_MAX_ATTEMPTS" ]; then
            kill -KILL "$_psp_pid" 2>/dev/null || true
            return 1
        fi
        # Yield to the dying presenter; see _progress_lock for why a failed
        # yield abandons the loop instead of retrying. Giving up here is the
        # conservative answer too: an unconfirmed presenter blocks the erase.
        sleep 0 || return 1
    done
    wait "$_psp_pid" 2>/dev/null || true
    return 0
}

# Background presenter: prints the task line once the command has outlived
# progress_delay, and prints nothing at all if the command finished first. Every
# early return here is a deliberate no-op, since a progress line is optional and
# any contention, signal, or write failure resolves to "print nothing".
_progress_present_after_delay() {
    _ppad_parent="$1"
    _ppad_workspace="$2"
    _ppad_columns="$3"
    _ppad_message="$4"

    _ppad_delay=''
    _ppad_lock_owned=0
    # A signalled presenter must not die holding the lock or leaving the delay
    # running: the parent would then spin out its own lock attempts and lose the
    # ability to erase a line this process already printed.
    trap '
        [ -z "$_ppad_delay" ] ||
            kill "$_ppad_delay" 2>/dev/null || true
        [ "$_ppad_lock_owned" -eq 0 ] ||
            rmdir "$_ppad_workspace/lock" 2>/dev/null || true
        exit 0
    ' HUP INT TERM

    # First critical section: confirm the line is still wanted, start the delay,
    # and publish its PID before releasing the workspace to the parent.
    _progress_lock "$_ppad_workspace" || return 0
    _ppad_lock_owned=1
    if [ ! -f "$_ppad_workspace/active" ]; then
        rmdir "$_ppad_workspace/lock" 2>/dev/null || true
        _ppad_lock_owned=0
        return 0
    fi
    progress_delay &
    _ppad_delay=$!
    if ! (
        printf '%s\n' "$_ppad_delay" > "$_ppad_workspace/delay-pid" 2>/dev/null
    ); then
        kill "$_ppad_delay" 2>/dev/null || true
        rmdir "$_ppad_workspace/lock" 2>/dev/null || true
        _ppad_lock_owned=0
        return 0
    fi
    if ! rmdir "$_ppad_workspace/lock" 2>/dev/null; then
        kill "$_ppad_delay" 2>/dev/null || true
        return 0
    fi
    _ppad_lock_owned=0
    # A non-zero wait means the parent killed the delay because its command
    # finished, so the line is no longer wanted. The liveness check additionally
    # keeps an orphaned presenter from printing under a shell that has exited.
    wait "$_ppad_delay" || return 0
    _ppad_delay=''
    kill -0 "$_ppad_parent" 2>/dev/null || return 0
    # Second critical section: re-check and record `shown` in the same section as
    # the print, so the parent observes the line and its evidence together.
    _progress_lock "$_ppad_workspace" || return 0
    _ppad_lock_owned=1
    if [ -f "$_ppad_workspace/active" ] &&
       kill -0 "$_ppad_parent" 2>/dev/null; then
        # The real terminal width completes the single-row invariant: the
        # capability check already proved the line fits the terminal, and this
        # keeps _print_indented_wrapped's default width from hard-wrapping a
        # line that is wider than that default but still fits the screen.
        COLUMNS="$_ppad_columns"
        if task_status info "$_ppad_message"; then
            (: > "$_ppad_workspace/shown") 2>/dev/null || true
        fi
    fi
    rmdir "$_ppad_workspace/lock" 2>/dev/null || true
    _ppad_lock_owned=0
    trap - HUP INT TERM
}

# run_with_progress <message> <command> [arguments...]
# The real command remains in the foreground and is run exactly once. On a
# verified capable terminal, a one-second delayed task line is erased before
# the caller renders its result. Redirected, CI, dumb, narrow, and otherwise
# unsupported output uses an ordinary permanent task line instead.
# This is the parent half of the workspace protocol documented above
# _progress_capabilities; every setup failure below abandons the delayed line
# and falls back to the permanent one rather than skipping the command.
run_with_progress() {
    _rwp_message="$1"
    shift

    if ! _progress_capabilities "$_rwp_message"; then
        [ "$DEBUG_MODE" -eq 1 ] || task_status info "$_rwp_message"
        "$@"
        return $?
    fi

    command -v mktemp >/dev/null 2>&1 || {
        task_status info "$_rwp_message"
        "$@"
        return $?
    }
    _rwp_parent=$$
    _rwp_tmp_parent="${TMPDIR:-/tmp}"
    if ! _rwp_workspace="$(
        umask 077
        mktemp -d "$_rwp_tmp_parent/scrooge-progress.XXXXXX"
    )"; then
        task_status info "$_rwp_message"
        "$@"
        return $?
    fi
    if ! (: > "$_rwp_workspace/active") 2>/dev/null; then
        rm -rf "$_rwp_workspace"
        task_status info "$_rwp_message"
        "$@"
        return $?
    fi

    _progress_present_after_delay \
        "$_rwp_parent" "$_rwp_workspace" "$PROGRESS_COLUMNS" \
        "$_rwp_message" &
    _rwp_presenter=$!

    if "$@"; then
        _rwp_status=0
    else
        _rwp_status=$?
    fi

    # The command has returned, so revoke the line. Doing that under the lock is
    # what makes the revocation binding: the presenter cannot be mid-print, and
    # every later check it makes will see `active` gone. Only then is it safe to
    # conclude that no further line can appear and that the row above is ours.
    _rwp_delay=''
    _rwp_can_erase=0
    if _progress_lock "$_rwp_workspace"; then
        if rm -f "$_rwp_workspace/active"; then
            _rwp_active_removed=1
        else
            _rwp_active_removed=0
        fi
        if [ -f "$_rwp_workspace/delay-pid" ]; then
            IFS= read -r _rwp_delay < "$_rwp_workspace/delay-pid" ||
                _rwp_delay=''
        fi
        if rmdir "$_rwp_workspace/lock" 2>/dev/null &&
           [ "$_rwp_active_removed" -eq 1 ]; then
            _rwp_can_erase=1
        fi
    else
        # Without the lock the revocation is still worth attempting (it shortens
        # the presenter's work), but it carries no guarantee, so _rwp_can_erase
        # stays 0 and the progress line is simply left on screen.
        rm -f "$_rwp_workspace/active" 2>/dev/null || true
        if [ -f "$_rwp_workspace/delay-pid" ]; then
            IFS= read -r _rwp_delay < "$_rwp_workspace/delay-pid" ||
                _rwp_delay=''
        fi
    fi
    # End the delay so the presenter wakes immediately instead of sleeping out
    # the remaining second; an absent or malformed PID just means it never
    # reached the point of publishing one.
    case "$_rwp_delay" in
        ''|*[!0-9]*) ;;
        *) kill "$_rwp_delay" 2>/dev/null || true ;;
    esac
    if _progress_stop_process "$_rwp_presenter"; then
        _rwp_presenter_stopped=1
    else
        _rwp_presenter_stopped=0
    fi
    # All three conditions are required: a reaped presenter cannot print after
    # the erase, a locked revocation means no line is still pending, and `shown`
    # proves a line was printed at all. Missing any one of them, the row above
    # may belong to the caller, so the progress line stays instead.
    if [ "$_rwp_presenter_stopped" -eq 1 ] &&
       [ "$_rwp_can_erase" -eq 1 ] &&
       [ -f "$_rwp_workspace/shown" ]; then
        printf '%s%s%s' \
            "$PROGRESS_CURSOR_UP" "$PROGRESS_CARRIAGE_RETURN" \
            "$PROGRESS_ERASE_LINE"
    fi
    # A presenter that outlived SIGKILL confirmation may still touch the
    # workspace, so leak the temporary directory rather than pull it out from
    # under a live process.
    if [ "$_rwp_presenter_stopped" -eq 1 ]; then
        rm -rf "$_rwp_workspace"
    fi
    return "$_rwp_status"
}

# Renders every task_status, guidance, and bullet line, wrapping prose onto the
# caller's continuation indent. Width is measured after removing color sequences
# because task prefixes and command_text embed them mid-line. sh never exports
# COLUMNS, so 100 is the width these scripts actually run at rather than a rare
# fallback; it is the deterministic width the test environments pin (see
# tests/conftest.py and tests/ui/harness/shell.py). The lower bound is cosmetic:
# it keeps an implausibly narrow width from degenerating into one word per line.
_print_indented_wrapped() {
    _piw_first="$1"
    _piw_continuation="$2"
    shift 2
    _piw_width="${COLUMNS:-100}"
    case "$_piw_width" in
        ''|*[!0-9]*) _piw_width=100 ;;
    esac
    [ "$_piw_width" -ge 20 ] || _piw_width=20
    printf '%s\n' "$*" | awk \
        -v first="$_piw_first" \
        -v continuation="$_piw_continuation" \
        -v width="$_piw_width" '
        {
            prefix = first
            line = prefix
            for (i = 1; i <= NF; i++) {
                separator = (line == prefix ? "" : " ")
                visible = line separator $i
                escape = sprintf("%c", 27)
                gsub(escape "\\[[0-9;]*m", "", visible)
                if (length(visible) > width && line != prefix) {
                    print line
                    prefix = continuation
                    line = prefix $i
                } else {
                    line = line separator $i
                }
            }
            print line
        }'
}

guidance() {
    _print_indented_wrapped '    ' '        ' "$@"
}

bullet() {
    _print_indented_wrapped '    - ' '        ' "$@"
}

end_operational_output() {
    printf '\n'
}

# run_action <command> [arguments...]
# Quiet by default; in debug mode the command owns the terminal streams.
run_action() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        "$@"
    else
        "$@" >/dev/null 2>&1
    fi
}

# run_captured <command> [arguments...]
# Exports the command's stdout through CAPTURED_COMMAND_OUTPUT and stderr through
# CAPTURED_COMMAND_STDERR. Both streams remain quiet normally and are mirrored to
# the terminal diagnostics stream after capture in debug mode. Using stderr for
# the mirror keeps stdout safe when a caller itself captures this helper's output.
# The command is always run exactly once, and its precise status is returned.
run_captured() {
    command -v mktemp >/dev/null 2>&1 || {
        printf '%s\n' "Error: mktemp is required for command capture." >&2
        return 1
    }
    _rc_parent="${TMPDIR:-/tmp}"
    if ! _rc_workspace="$(
        umask 077
        mktemp -d "$_rc_parent/scrooge-capture.XXXXXX"
    )"; then
        printf '%s\n' "Error: Could not create a private command-capture workspace." >&2
        return 1
    fi
    _rc_stdout="$_rc_workspace/stdout"
    _rc_stderr="$_rc_workspace/stderr"
    if "$@" >"$_rc_stdout" 2>"$_rc_stderr"; then
        _rc_status=0
    else
        _rc_status=$?
    fi
    CAPTURED_COMMAND_OUTPUT="$(cat "$_rc_stdout")"
    CAPTURED_COMMAND_STDERR="$(cat "$_rc_stderr")"
    if [ "$DEBUG_MODE" -eq 1 ]; then
        cat "$_rc_stdout" >&2
        cat "$_rc_stderr" >&2
    fi
    rm -rf "$_rc_workspace"
    return "$_rc_status"
}

path_entry_exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

require_regular_owned_file() {
    if [ -L "$1" ] || [ ! -f "$1" ]; then
        printf '%s\n' "Error: Required project file must be a regular file: $1" >&2
        return 1
    fi
}

reject_project_venv_symlink() {
    if [ -L "$BASE_DIR/venv" ]; then
        printf '%s\n' \
            "Error: $BASE_DIR/venv must be a project-owned directory, not a symlink." >&2
        printf '%s\n' \
            "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')." >&2
        return 1
    fi
}

# reject_project_venv_symlink_for <interpreter>
# The guard applies exactly when the project venv is the interpreter in use.
# An explicitly selected external interpreter is outside the project venv, so
# there is nothing for the guard to protect: CI runs the checks with no ./venv
# at all, and plugin isolation runs each target against its own throwaway venv.
# The comparison is lexical, so a non-canonical spelling of the project venv
# skips it. That is acceptable for the read-only checks here, where the override
# is a deliberate act by whoever set it; plugin-create.sh states why the wizard
# keeps an unconditional guard instead.
reject_project_venv_symlink_for() {
    [ "$1" != "$BASE_DIR/venv/bin/python3" ] || reject_project_venv_symlink
}

unit_name() {
    printf '%s-scraper.%s' "$1" "$2"
}

is_valid_target() {
    case "$1" in
        ''|[!a-z]*|*[!a-z0-9_]*) return 1 ;;
        *) return 0 ;;
    esac
}

require_valid_target() {
    if ! is_valid_target "$1"; then
        printf '%s\n' \
            "Error: Invalid target '$1' (expected a nonblank snake_case name)." >&2
        return 1
    fi
}

# Target streams: newline-delimited text used as an ordered, unique set, because
# POSIX sh has no arrays. (runtime.sh answers the same problem with a numbered
# positional queue instead, since it forwards argv rather than holding a set.)
#
# The empty string is the empty set. Newline is an IFS whitespace character, so
# blank lines collapse and an item can never be empty: that is why an accumulator
# can start at '' and why stream_contains '' never matches. Membership compares
# whole items, so a prefix of a target is not that target.
#
# Every consumer -- these helpers and the loops in the command scripts -- reads a
# stream the same way: set IFS to a lone newline, expand the stream unquoted so it
# splits on newlines alone, then restore IFS before leaving, including on an early
# return, so no caller inherits it. Items may contain spaces, since
# plugin_stream_value returns tab-delimited snapshot values that do, but the
# unquoted expansion still globs. Keeping whitespace and glob characters out is
# the producer's job, and there are two producers with the same alphabet:
# is_valid_target gates on-disk unit names and target flags, while catalog rows
# arrive pre-validated from the Python side's SNAKE_CASE_KEY. A consumer cannot
# see which kind of stream it holds, so every one of them sets IFS even where
# today's items could not split under the default.
#
# That IFS assignment is spelled over two lines with the closing quote at column 0,
# because POSIX sh has no $'\n' escape and the newline has to be typed inside the
# quotes. Indenting the closing quote would put that indentation into IFS as well
# and restore splitting on spaces, so it stays flush left inside indented bodies.
#
# ShellCheck does not warn about an unquoted expansion in a 'for' word list, so
# these loops need no SC2086 directive. One is only needed for an unquoted
# expansion in command arguments, which this layer no longer has.
stream_contains() {
    _sc_needle="$1"
    _sc_stream="$2"
    _sc_old_ifs="$IFS"
    IFS='
'
    for _sc_item in $_sc_stream; do
        if [ "$_sc_item" = "$_sc_needle" ]; then
            IFS="$_sc_old_ifs"
            return 0
        fi
    done
    IFS="$_sc_old_ifs"
    return 1
}

stream_add_unique() {
    _sau_stream="$1"
    _sau_item="$2"
    if [ -n "$_sau_stream" ]; then
        printf '%s\n' "$_sau_stream"
    fi
    stream_contains "$_sau_item" "$_sau_stream" || printf '%s\n' "$_sau_item"
}

stream_union() {
    _su_result=''
    _su_old_ifs="$IFS"
    IFS='
'
    for _su_item in $1 ${2:-}; do
        if ! stream_contains "$_su_item" "$_su_result"; then
            _su_result="$(stream_add_unique "$_su_result" "$_su_item")"
        fi
    done
    IFS="$_su_old_ifs"
    [ -z "$_su_result" ] || printf '%s\n' "$_su_result"
}

stream_for_display() {
    _sfd_old_ifs="$IFS"
    IFS='
'
    for _sfd_item in $1; do
        printf '%s ' "$_sfd_item"
    done
    IFS="$_sfd_old_ifs"
}

plugin_stream_value() {
    _psv_target="$1"
    _psv_rows="$2"
    _psv_tab="$(printf '\t')"
    _psv_old_ifs="$IFS"
    IFS='
'
    for _psv_row in $_psv_rows; do
        if [ "${_psv_row%%"$_psv_tab"*}" = "$_psv_target" ]; then
            IFS="$_psv_old_ifs"
            printf '%s' "${_psv_row#*"$_psv_tab"}"
            return 0
        fi
    done
    IFS="$_psv_old_ifs"
    return 1
}

# Named rather than inlined so parse_target_flags can route it through
# run_action like the target validator beside it.
report_invalid_argument() {
    printf '%s\n' "Error: Invalid argument: $1" >&2
    return 1
}

# parse_target_flags <arguments...>
# Exports TARGET_FLAGS, TARGET_FLAGS_EXPLICIT, TARGET_HELP_REQUESTED, and the
# shared DEBUG_MODE. SCROOGE_INTERNAL_DEBUG propagates debug state only to child
# project scripts and is intentionally not a public command-line interface.
# Help is recognized in any position before other arguments are interpreted.
#
# DEBUG_MODE is cleared before the scan, so an inherited SCROOGE_INTERNAL_DEBUG
# does not survive a public argument parse: --debug is a per-invocation flag, and
# an ambient variable must not silently turn debug on for a command the user
# invoked directly. install.sh's deferred update context is the one caller that
# must defeat this, and it restores the inherited value itself.
#
# This function gates its own argument diagnostics rather than letting callers
# wrap it in run_action, because a wrapper reads DEBUG_MODE to choose redirection
# before the scan below sets it -- which is why --debug could not surface them.
# Routing them through run_action here is safe for the opposite reason: the first
# pass has already resolved DEBUG_MODE by the time the second pass reports.
parse_target_flags() {
    TARGET_FLAGS=''
    TARGET_FLAGS_EXPLICIT=0
    TARGET_HELP_REQUESTED=0
    DEBUG_MODE=0
    for _ptf_arg in "$@"; do
        case "$_ptf_arg" in
            -h|--help) TARGET_HELP_REQUESTED=1 ;;
            --debug) DEBUG_MODE=1 ;;
        esac
    done
    SCROOGE_INTERNAL_DEBUG="$DEBUG_MODE"
    export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
    [ "$TARGET_HELP_REQUESTED" -eq 0 ] || return 0

    for _ptf_arg in "$@"; do
        case "$_ptf_arg" in
            --debug) ;;
            --)
                run_action report_invalid_argument "$_ptf_arg"
                return 1
                ;;
            --?*)
                _ptf_target="${_ptf_arg#--}"
                run_action require_valid_target "$_ptf_target" || return 1
                TARGET_FLAGS_EXPLICIT=1
                TARGET_FLAGS="$(stream_add_unique "$TARGET_FLAGS" "$_ptf_target")"
                ;;
            *)
                run_action report_invalid_argument "$_ptf_arg"
                return 1
                ;;
        esac
    done
}

# Catalog access defaults to the project venv. install.sh overrides
# CATALOG_PYTHON for its import-light, pre-venv validation pass.
catalog_cli() {
    _cc_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    reject_project_venv_symlink_for "$_cc_python" || return 1
    case "$_cc_python" in
        */*) [ -x "$_cc_python" ] || return 1 ;;
        *) command -v "$_cc_python" >/dev/null 2>&1 || return 1 ;;
    esac
    PYTHONPATH="$BASE_DIR/src" "$_cc_python" -m core.scrapers.tooling.cli "$@"
}

PLUGIN_CATALOG_STATE=0
PLUGIN_CATALOG_DATA=''
PLUGIN_SCHEDULE_STATE=0
PLUGIN_SCHEDULE_DATA=''

reset_catalog_cache() {
    PLUGIN_CATALOG_STATE=0
    PLUGIN_CATALOG_DATA=''
    PLUGIN_SCHEDULE_STATE=0
    PLUGIN_SCHEDULE_DATA=''
}

# Lazily loads the catalog once and discards the command's own output, so a
# caller that only needs the data stays quiet. Callers that must also show that
# output use prime_plugin_catalog below instead.
load_plugin_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if PLUGIN_CATALOG_DATA="$(catalog_cli catalog 2>/dev/null)"; then
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_STATE=2
    PLUGIN_CATALOG_DATA=''
    return 1
}

# Fills the same cache on the same contract, but through run_captured, which
# mirrors the command's output in debug mode. Because load_plugin_catalog is
# silent by design, this eager call is the only point at which --debug can show
# the underlying catalog command, and the cache it leaves warm is what keeps
# every later lazy call from running that command a second time. Commands with
# a --debug mode therefore prime here before any shared helper can reach the
# lazy loader; the status is identical, so a caller may branch on it or ignore
# it. A caller that needs a re-read rather than a first read must
# reset_catalog_cache first, since this memoizes exactly like the lazy loader.
prime_plugin_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if run_captured catalog_cli catalog; then
        PLUGIN_CATALOG_DATA="$CAPTURED_COMMAND_OUTPUT"
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_STATE=2
    PLUGIN_CATALOG_DATA=''
    return 1
}

# Catalog snapshot columns, as the accessors below address them. The producer
# (core.scrapers.tooling.cli catalog_rows) owns the contract; this legend exists
# so a field number is editable here without reading the Python:
#
#   $1 target  $2 display_name  $3 example_config_path  $4 requirements_path
#
# Every column but $4 is always populated; $4 is empty for a plugin with no
# private dependencies, which is why list_plugin_requirements filters on it.
# The snapshot reads no target config, so these values survive a broken config.
plugin_catalog() {
    load_plugin_catalog || return 1
    [ -z "$PLUGIN_CATALOG_DATA" ] || printf '%s\n' "$PLUGIN_CATALOG_DATA"
}

# Each accessor loads before it pipes, even though plugin_catalog already
# guards: a pipeline's status is its last command's, and awk succeeds on empty
# input, so piping alone would report an unloadable catalog as an empty success.
# Callers branch on these, so the load has to be reached outside the pipeline.
# The loader is memoized, so the extra call costs nothing after the first.
list_plugins() {
    load_plugin_catalog || return 1
    plugin_catalog | awk -F '\t' '{ print $1 }'
}

plugin_display_name() {
    load_plugin_catalog || return 1
    plugin_catalog | awk -F '\t' -v target="$1" '$1 == target { print $2; exit }'
}

list_plugin_examples() {
    load_plugin_catalog || return 1
    plugin_catalog | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_plugin_requirements() {
    load_plugin_catalog || return 1
    plugin_catalog | awk -F '\t' '$4 != "" { print $1 "\t" $4 }'
}

load_plugin_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if PLUGIN_SCHEDULE_DATA="$(
        catalog_cli schedules --config-dir "$BASE_DIR/config" 2>/dev/null
    )"; then
        PLUGIN_SCHEDULE_STATE=1
        return 0
    fi
    PLUGIN_SCHEDULE_STATE=2
    PLUGIN_SCHEDULE_DATA=''
    return 1
}

# The schedule-report counterpart of prime_plugin_catalog, on the same contract
# and for the same reason; see that function for why the eager call exists.
prime_plugin_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if run_captured catalog_cli schedules --config-dir "$BASE_DIR/config"; then
        PLUGIN_SCHEDULE_DATA="$CAPTURED_COMMAND_OUTPUT"
        PLUGIN_SCHEDULE_STATE=1
        return 0
    fi
    PLUGIN_SCHEDULE_STATE=2
    PLUGIN_SCHEDULE_DATA=''
    return 1
}

# Schedule report columns, as the accessors below address them. The producer
# (core.scrapers.tooling.cli schedule_rows) owns the contract:
#
#   $1 target  $2 on_calendar  $3 status  $4 error
#
# $3 is the branch key, with five values. 'error' means the target's config could
# not be read at all: $2 is empty and $4 carries the message, so a config failure
# is isolated to its own row and the other targets still report. The remaining
# four are the execution_interval resolution: 'ok' (configured), 'default' (no
# value set), 'invalid' (a bad configured value), and 'nocfg' (no config file on
# disk). All four carry a usable $2, because the producer falls back to the
# plugin's canonical default_interval rather than leaving a target unrenderable.
#
# The report states what was resolved, not what to do about it; each consuming
# command owns that policy and they legitimately differ. install.sh (and update.sh
# through it) provisions every non-'error' row, so a target is never left without
# a timer over a typo or an unwritten config, and warns on 'invalid'. schedule.sh
# retunes a timer the user already has, so it applies 'ok'/'default' and warns on
# 'invalid'/'nocfg' rather than downgrading a working schedule to the fallback.
# Each script's own header explains why it chose its side.
plugin_schedules() {
    load_plugin_schedules || return 1
    [ -z "$PLUGIN_SCHEDULE_DATA" ] || printf '%s\n' "$PLUGIN_SCHEDULE_DATA"
}

# The same pipeline-status rule applies here; see the catalog accessors above.
list_plugin_schedules() {
    load_plugin_schedules || return 1
    plugin_schedules | awk -F '\t' '$3 != "error" { print $1 "\t" $2 }'
}

list_interval_status() {
    load_plugin_schedules || return 1
    plugin_schedules | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_schedule_errors() {
    load_plugin_schedules || return 1
    plugin_schedules | awk -F '\t' '$3 == "error" { print $1 "\t" $4 }'
}

list_supported_intervals() {
    catalog_cli intervals 2>/dev/null
}

catalog_diagnose() {
    _cd_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    reject_project_venv_symlink_for "$_cd_python" || return 1
    if [ "$_cd_python" = "$BASE_DIR/venv/bin/python3" ] && [ ! -x "$_cd_python" ]; then
        printf '%s\n' \
            "Error: Cannot read the plugin catalog - the Python environment looks missing or broken." >&2
        printf '%s\n' \
            "Reinstall it with: $(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')" >&2
        return 1
    fi
    printf '%s\n' "Error: The scraper plugin catalog could not be loaded." >&2
    if catalog_cli diagnose >&2; then
        printf '%s\n' "The catalog is readable now; retry the command." >&2
    else
        printf '%s\n' \
            "Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry." >&2
    fi
    return 1
}

# select_targets <registered|installed_registered_timers|installed_services|installed_union>
# Requires systemd.sh for installed policies.
#
# This helper owns the mechanics of each policy, not the reason a command picks
# one; each entry point states that in its own file header, since the choice
# follows from what that command does to a unit (enable/schedule act on a timer
# and need its plugin, stop acts on a service, disable/uninstall tear down
# whatever is installed).
#
# Exports five values, all initialized before any early return: SELECTED_TARGETS
# is the result, SELECTED_REGISTERED, SELECTED_INSTALLED, and SELECTED_KNOWN
# (their union) are the sets the policy was resolved against, and
# SELECTED_CATALOG_LOADED records whether the catalog answered at all. They are
# part of the contract, not scratch state: callers wrap this in run_action, which
# suppresses stderr in quiet mode, so the diagnostics printed below reach the user
# only under --debug. In quiet mode the caller re-renders the same diagnosis
# through task_status from these values, and the sets also drive the success-path
# "registered but not installed" notices. Renaming these five breaks every
# renderer named below.
#
# Where that re-rendering lives follows how much of it is actually per-command:
# show_timer_selection_failure below owns the installed_registered_timers
# rendering, because its only per-command word is the command name and it
# restates catalog_diagnose's broken-environment policy, which must not drift.
# The teardown policies (installed_services, installed_union) keep a local
# show_selection_failure in disable.sh, stop.sh, and uninstall.sh instead: those
# differ in presentation vocabulary alone (services versus units) and carry no
# shared policy, so a parameter per noun would buy nothing.
select_targets() {
    _st_policy="$1"
    # Initialize the whole contract before any early return so a caller rendering
    # a failure never reads a stale set from an earlier call.
    SELECTED_TARGETS=''
    SELECTED_INSTALLED=''
    SELECTED_KNOWN=''
    # An unloadable catalog and one that legitimately registers nothing are
    # different situations with the same empty set, so ask rather than infer.
    # The old proxy -- units installed but nothing registered -- both missed a
    # broken catalog when nothing was installed yet and cried "catalog could not
    # be loaded" at a healthy catalog that simply has no plugins left.
    SELECTED_CATALOG_LOADED=1
    if ! SELECTED_REGISTERED="$(list_plugins 2>/dev/null)"; then
        SELECTED_REGISTERED=''
        SELECTED_CATALOG_LOADED=0
    fi
    case "$_st_policy" in
        registered) ;;
        installed_registered_timers)
            SELECTED_INSTALLED="$(list_installed_units timer)" || return 1
            # Only this policy needs the catalog: it resolves an installed timer
            # to the plugin behind it. The teardown policies below act on units
            # alone and must keep working while the catalog is broken.
            if [ "$SELECTED_CATALOG_LOADED" -eq 0 ]; then
                catalog_diagnose || return 1
            fi
            ;;
        installed_services)
            SELECTED_INSTALLED="$(list_installed_units service)" || return 1
            ;;
        installed_union)
            SELECTED_INSTALLED="$(list_installed_targets)" || return 1
            ;;
        *) return 2 ;;
    esac
    SELECTED_KNOWN="$(stream_union "$SELECTED_REGISTERED" "$SELECTED_INSTALLED")"

    if [ "$TARGET_FLAGS_EXPLICIT" -eq 0 ]; then
        case "$_st_policy" in
            registered) SELECTED_TARGETS="$SELECTED_REGISTERED" ;;
            installed_registered_timers)
                _st_old_ifs="$IFS"
                IFS='
'
                for _st_target in $SELECTED_INSTALLED; do
                    if stream_contains "$_st_target" "$SELECTED_REGISTERED"; then
                        SELECTED_TARGETS="$(
                            stream_add_unique "$SELECTED_TARGETS" "$_st_target"
                        )"
                    fi
                done
                IFS="$_st_old_ifs"
                ;;
            *) SELECTED_TARGETS="$SELECTED_INSTALLED" ;;
        esac
        return 0
    fi

    _st_old_ifs="$IFS"
    IFS='
'
    for _st_target in $TARGET_FLAGS; do
        case "$_st_policy" in
            registered)
                if ! stream_contains "$_st_target" "$SELECTED_REGISTERED"; then
                    printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    printf '%s\n' \
                        "Available targets: $(stream_for_display "$SELECTED_REGISTERED")" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                ;;
            installed_registered_timers)
                if ! stream_contains "$_st_target" "$SELECTED_INSTALLED"; then
                    if stream_contains "$_st_target" "$SELECTED_REGISTERED"; then
                        printf '%s\n' \
                            "Error: '$_st_target' is registered but not installed." >&2
                    else
                        printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    fi
                    IFS="$_st_old_ifs"
                    return 1
                fi
                if ! stream_contains "$_st_target" "$SELECTED_REGISTERED"; then
                    printf '%s\n' \
                        "Error: '$_st_target' is installed but no longer registered (orphan)." >&2
                    printf '%s\n' \
                        "Remove it with: $(command_text "./scrooge-alert uninstall --$_st_target")" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                ;;
            installed_services|installed_union)
                if ! stream_contains "$_st_target" "$SELECTED_KNOWN"; then
                    printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    printf '%s\n' \
                        "Available targets: $(stream_for_display "$SELECTED_KNOWN")" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                if ! stream_contains "$_st_target" "$SELECTED_INSTALLED"; then
                    printf '%s\n' \
                        "[$_st_target] is registered but not installed - nothing to do."
                    continue
                fi
                ;;
        esac
        SELECTED_TARGETS="$(stream_add_unique "$SELECTED_TARGETS" "$_st_target")"
    done
    IFS="$_st_old_ifs"
}

# show_timer_selection_failure <command>
# Quiet-mode rendering of a failed `select_targets installed_registered_timers`,
# shared by enable.sh and schedule.sh because the command name is the only thing
# that differs between them. The broken-catalog branch is the task_status mirror
# of catalog_diagnose: same two situations (an unusable project venv versus a
# readable venv with an offending package), same two remediations, so the pair
# must be changed together.
show_timer_selection_failure() {
    _stsf_command="$1"
    if [ "$SELECTED_CATALOG_LOADED" -eq 0 ]; then
        task_status failure "The target catalog could not be loaded."
        if [ ! -x "$BASE_DIR/venv/bin/python3" ] || [ -L "$BASE_DIR/venv" ]; then
            task_status warning \
                "Reinstall it with: $(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')"
        else
            task_status warning \
                "Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry."
        fi
        return
    fi

    _stsf_old_ifs="$IFS"
    IFS='
'
    for _stsf_target in $TARGET_FLAGS; do
        if stream_contains "$_stsf_target" "$SELECTED_INSTALLED"; then
            if ! stream_contains "$_stsf_target" "$SELECTED_REGISTERED"; then
                task_status failure \
                    "'$_stsf_target' is installed but no longer registered (orphan)."
                task_status warning \
                    "Remove it with: $(command_text "./scrooge-alert uninstall --$_stsf_target")"
                IFS="$_stsf_old_ifs"
                return
            fi
        elif stream_contains "$_stsf_target" "$SELECTED_REGISTERED"; then
            task_status failure \
                "'$_stsf_target' is registered but not installed."
            task_status warning \
                "Install it with: $(command_text "./scrooge-alert install --$_stsf_target")"
            IFS="$_stsf_old_ifs"
            return
        else
            task_status failure "Unknown target '$_stsf_target'."
            task_status info \
                "Run $(command_text "./scrooge-alert $_stsf_command --help") for available targets."
            IFS="$_stsf_old_ifs"
            return
        fi
    done
    IFS="$_stsf_old_ifs"
    task_status failure "The installed target timers could not be selected safely."
    task_status info \
        "Run $(command_text "./scrooge-alert $_stsf_command --debug") for underlying diagnostics."
}

# show_uninstalled_notices <verb>
# Success-path counterpart for the teardown policies: select_targets skips an
# explicitly named target that is registered but has no installed unit, and this
# reports each one as informational rather than failing the command. The verb
# ("disable", "stop", "remove") is the only per-command word, so the condition --
# which is the SELECTED_REGISTERED/SELECTED_INSTALLED contract itself -- is stated
# once here instead of once per teardown script.
show_uninstalled_notices() {
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] || return 0
    _sun_verb="$1"
    _sun_old_ifs="$IFS"
    IFS='
'
    for _sun_target in $TARGET_FLAGS; do
        if stream_contains "$_sun_target" "$SELECTED_REGISTERED" &&
            ! stream_contains "$_sun_target" "$SELECTED_INSTALLED"; then
            task_status info \
                "[$_sun_target] Target is registered but not installed; nothing to $_sun_verb."
        fi
    done
    IFS="$_sun_old_ifs"
}
