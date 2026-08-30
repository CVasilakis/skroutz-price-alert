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

stream_contains() {
    _sc_needle="$1"
    _sc_stream="$2"
    _sc_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
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
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
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
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
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
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
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

# parse_target_flags <arguments...>
# Exports TARGET_FLAGS, TARGET_FLAGS_EXPLICIT, TARGET_HELP_REQUESTED, and the
# shared DEBUG_MODE. SCROOGE_INTERNAL_DEBUG propagates debug state only to child
# project scripts and is intentionally not a public command-line interface.
# Help is recognized in any position before other arguments are interpreted.
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
                printf '%s\n' "Error: Invalid argument: $_ptf_arg" >&2
                return 1
                ;;
            --?*)
                _ptf_target="${_ptf_arg#--}"
                require_valid_target "$_ptf_target" || return 1
                TARGET_FLAGS_EXPLICIT=1
                TARGET_FLAGS="$(stream_add_unique "$TARGET_FLAGS" "$_ptf_target")"
                ;;
            *)
                printf '%s\n' "Error: Invalid argument: $_ptf_arg" >&2
                return 1
                ;;
        esac
    done
}

# Catalog access defaults to the project venv. install.sh overrides
# CATALOG_PYTHON for its import-light, pre-venv validation pass.
catalog_cli() {
    _cc_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    if [ "$_cc_python" = "$BASE_DIR/venv/bin/python3" ]; then
        reject_project_venv_symlink || return 1
    fi
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
# caller that only needs the data stays quiet. That silence is why the scripts
# with a --debug mode prime this cache themselves before any shared helper can
# reach it: priming through run_captured is the only point at which the
# underlying command's output can still be mirrored, and the warm cache then
# keeps every later lazy call from running the command a second time.
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

list_plugins() {
    plugin_catalog | awk -F '\t' '{ print $1 }'
}

plugin_display_name() {
    plugin_catalog | awk -F '\t' -v target="$1" '$1 == target { print $2; exit }'
}

list_plugin_examples() {
    plugin_catalog | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_plugin_requirements() {
    plugin_catalog | awk -F '\t' '$4 != "" { print $1 "\t" $4 }'
}

load_plugin_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if PLUGIN_SCHEDULE_DATA="$(
        catalog_cli schedules --config-dir "$BASE_DIR/config"
    )"; then
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
# four are the execution_interval resolution: 'ok' and 'default' are schedulable
# and carry a usable $2, while 'invalid' (a bad configured value) and 'nocfg' (no
# config file on disk) warn and leave an existing timer unchanged. schedule.sh
# branches on exactly this vocabulary.
plugin_schedules() {
    load_plugin_schedules || return 1
    [ -z "$PLUGIN_SCHEDULE_DATA" ] || printf '%s\n' "$PLUGIN_SCHEDULE_DATA"
}

list_plugin_schedules() {
    plugin_schedules | awk -F '\t' '$3 != "error" { print $1 "\t" $2 }'
}

list_interval_status() {
    plugin_schedules | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_schedule_errors() {
    plugin_schedules | awk -F '\t' '$3 == "error" { print $1 "\t" $4 }'
}

list_supported_intervals() {
    catalog_cli intervals 2>/dev/null
}

catalog_diagnose() {
    _cd_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    if [ "$_cd_python" = "$BASE_DIR/venv/bin/python3" ]; then
        if [ -L "$BASE_DIR/venv" ]; then
            reject_project_venv_symlink
            return 1
        fi
        if [ ! -x "$_cd_python" ]; then
            printf '%s\n' \
                "Error: Cannot read the plugin catalog - the Python environment looks missing or broken." >&2
            printf '%s\n' \
                "Reinstall it with: $(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')" >&2
            return 1
        fi
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
# Exports four values, all initialized before any early return: SELECTED_TARGETS
# is the result, and SELECTED_REGISTERED, SELECTED_INSTALLED, and SELECTED_KNOWN
# (their union) are the sets the policy was resolved against. The three sets are
# part of the contract, not scratch state: callers wrap this in run_action, which
# suppresses stderr in quiet mode, so the diagnostics printed below reach the user
# only under --debug. In quiet mode each lifecycle script re-renders the same
# diagnosis through task_status from these sets, in its own per-command wording
# (timers/services/units, and which remediation command to name). The sets also
# drive the success-path "registered but not installed" notices. Keeping the
# rendering in the scripts keeps per-command presentation vocabulary out of this
# shared helper; renaming these four breaks those callers.
select_targets() {
    _st_policy="$1"
    # Initialize the whole contract before any early return so a caller rendering
    # a failure never reads a stale set from an earlier call.
    SELECTED_TARGETS=''
    SELECTED_INSTALLED=''
    SELECTED_KNOWN=''
    SELECTED_REGISTERED="$(list_plugins 2>/dev/null || true)"
    case "$_st_policy" in
        registered) ;;
        installed_registered_timers)
            SELECTED_INSTALLED="$(list_installed_units timer)" || return 1
            if [ -n "$SELECTED_INSTALLED" ] && [ -z "$SELECTED_REGISTERED" ]; then
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
                # shellcheck disable=SC2086
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
    # shellcheck disable=SC2086
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
