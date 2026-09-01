#!/bin/sh
# Stop the currently running executions of installed targets.
#
# Selection policy: installed_services, installed service units only. Stopping
# acts on the service, so an installed timer with no service has no execution to
# stop and is not selectable. Registration is deliberately not required: an
# orphan's service can still be running, and must stay stoppable.

set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"

print_help() {
    load_plugin_catalog || true
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_units service 2>/dev/null || true)"
    _ph_known="$(stream_union "$_ph_registered" "$_ph_installed")"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = stop ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert stop [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: stop.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n\n' "Stop currently running installed scraper services."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    for _ph_target in $_ph_known; do
        printf '  --%-15s Stop only the %s target\n' "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

stop_finish() {
    end_operational_output
    exit "$1"
}

# Quiet-mode rendering of a failed `select_targets installed_services`; see that
# helper in common.sh for why the teardown scripts each keep their own copy
# rather than sharing one. disable.sh and uninstall.sh hold the same shape, in
# their own unit vocabulary.
show_selection_failure() {
    _ssf_old_ifs="$IFS"
    IFS='
'
    for _ssf_target in $TARGET_FLAGS; do
        if ! stream_contains "$_ssf_target" "$SELECTED_REGISTERED" &&
            ! stream_contains "$_ssf_target" "$SELECTED_INSTALLED"; then
            task_status failure "Unknown target '$_ssf_target'."
            if [ -n "$SELECTED_KNOWN" ]; then
                task_status info \
                    "Available targets: $(stream_for_display "$SELECTED_KNOWN")"
            else
                task_status info "Run $(command_text './scrooge-alert stop --help') for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    task_status failure "The installed target services could not be selected safely."
    task_status info "Run $(command_text './scrooge-alert stop --debug') for underlying diagnostics."
}

# The query runs in a command substitution, so systemd_property's own diagnostic
# would bypass run_action and reach the terminal in quiet mode. enable.sh reads
# the paired timer the same way.
capture_service_state() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        service_state "$1"
    else
        service_state "$1" 2>/dev/null
    fi
}

HELP_REQUESTED=0
for argument in "$@"; do
    case "$argument" in
        -h|--help) HELP_REQUESTED=1 ;;
    esac
done
if [ "$HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi

begin_operational_output
if ! parse_target_flags "$@"; then
    section_heading success "Stop preflight"
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert stop --help') for usage."
    stop_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Stop preflight"
    task_status failure "systemctl (systemd) is not installed or not available."
    task_status warning "Install systemd, then retry this command."
    stop_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    prime_plugin_catalog || true
fi
if ! run_action select_targets installed_services; then
    section_heading success "Stop preflight"
    show_selection_failure
    stop_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Active executions"
show_uninstalled_notices stop
if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        task_status info "No installed target services found."
    stop_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
for plugin in $PLUGINS; do
    if ! state="$(capture_service_state "$plugin")"; then
        task_status failure "[$plugin] Could not determine the service state."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert stop --debug --$plugin") for underlying diagnostics."
        FAILED=1
    elif state_is_stopped "$state"; then
        task_status info "[$plugin] No active background execution detected."
    else
        if run_with_progress \
            "[$plugin] Stopping active background execution..." \
            run_action stop_one "$plugin"; then
            task_status success "[$plugin] Active execution stopped."
        else
            task_status failure "[$plugin] Active execution could not be stopped."
            task_status info \
                "[$plugin] Run $(command_text "./scrooge-alert stop --debug --$plugin") for underlying diagnostics."
            FAILED=1
        fi
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || stop_finish 1
printf '\n'
section_heading success "Optional controls"
task_status info "To disable future executions, run: $(command_text './scrooge-alert disable')"
stop_finish 0
