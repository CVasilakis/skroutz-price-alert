#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"

# shellcheck disable=SC2034  # cache values are consumed by shared catalog helpers
load_plugin_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if [ "$DEBUG_MODE" -eq 1 ]; then
        if run_captured catalog_cli catalog; then
            PLUGIN_CATALOG_DATA="$CAPTURED_COMMAND_OUTPUT"
            PLUGIN_CATALOG_STATE=1
            return 0
        fi
    elif PLUGIN_CATALOG_DATA="$(catalog_cli catalog 2>/dev/null)"; then
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_STATE=2
    PLUGIN_CATALOG_DATA=''
    return 1
}

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
    # shellcheck disable=SC2086
    for _ph_target in $_ph_known; do
        printf '  --%-15s Stop only the %s target\n' "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

stop_task() {
    _st_kind="$1"
    shift
    case "$_st_kind" in
        success) _st_marker='v'; _st_color="$GREEN" ;;
        failure) _st_marker='x'; _st_color="$RED" ;;
        info) _st_marker='i'; _st_color="$CYAN" ;;
        warning) _st_marker='!'; _st_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _st_prefix="    ${_st_color}[${_st_marker}]${NC} "
    _print_indented_wrapped "$_st_prefix" '        ' "$@"
}

stop_finish() {
    end_operational_output
    exit "$1"
}

show_selection_failure() {
    _ssf_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ssf_target in $TARGET_FLAGS; do
        if ! stream_contains "$_ssf_target" "${_st_registered:-}" &&
            ! stream_contains "$_ssf_target" "${_st_installed:-}"; then
            stop_task failure "Unknown target '$_ssf_target'."
            if [ -n "${_st_known:-}" ]; then
                stop_task info \
                    "Available targets: $(stream_for_display "$_st_known")"
            else
                stop_task info "Run $(command_text './scrooge-alert stop --help') for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    stop_task failure "The installed target services could not be selected safely."
    stop_task info "Run $(command_text './scrooge-alert stop --debug') for underlying diagnostics."
}

show_uninstalled_notices() {
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] || return 0
    _sun_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _sun_target in $TARGET_FLAGS; do
        if stream_contains "$_sun_target" "${_st_registered:-}" &&
            ! stream_contains "$_sun_target" "${_st_installed:-}"; then
            stop_task info \
                "[$_sun_target] Target is registered but not installed; nothing to stop."
        fi
    done
    IFS="$_sun_old_ifs"
}

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
    DEBUG_MODE=0
    SCROOGE_INTERNAL_DEBUG=0
    export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
    print_help
    exit 0
fi

begin_operational_output
if ! run_action parse_target_flags "$@"; then
    section_heading success "Stop preflight"
    stop_task failure "The command-line arguments are invalid."
    stop_task info "Run $(command_text './scrooge-alert stop --help') for usage."
    stop_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Stop preflight"
    stop_task failure "systemctl (systemd) is not installed or not available."
    stop_task warning "Install systemd, then retry this command."
    stop_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    load_plugin_catalog || true
fi
if ! run_action select_targets installed_services; then
    section_heading success "Stop preflight"
    show_selection_failure
    stop_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Active executions"
show_uninstalled_notices
if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        stop_task info "No installed target services found."
    stop_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    if ! state="$(capture_service_state "$plugin")"; then
        stop_task failure "[$plugin] Could not determine the service state."
        stop_task info \
            "[$plugin] Run $(command_text "./scrooge-alert stop --debug --$plugin") for underlying diagnostics."
        FAILED=1
    elif state_is_stopped "$state"; then
        stop_task info "[$plugin] No active background execution detected."
    else
        if run_with_progress \
            "[$plugin] Stopping active background execution..." \
            run_action stop_one "$plugin"; then
            stop_task success "[$plugin] Active execution stopped."
        else
            stop_task failure "[$plugin] Active execution could not be stopped."
            stop_task info \
                "[$plugin] Run $(command_text "./scrooge-alert stop --debug --$plugin") for underlying diagnostics."
            FAILED=1
        fi
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || stop_finish 1
printf '\n'
section_heading success "Optional controls"
stop_task info "To disable future executions, run: $(command_text './scrooge-alert disable')"
stop_finish 0
