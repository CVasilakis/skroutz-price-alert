#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"

# Debug-mode prime for the shared catalog cache; see load_plugin_catalog in
# lib/common.sh for why the eager load is what makes --debug show this output.
# shellcheck disable=SC2034  # cache values are consumed by shared catalog helpers
disable_load_catalog() {
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

print_help() {
    load_plugin_catalog || true
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_targets 2>/dev/null || true)"
    _ph_known="$(stream_union "$_ph_registered" "$_ph_installed")"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = disable ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert disable [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: disable.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n' "Stop and disable installed scraper timer/service pairs."
    printf '%s\n\n' "Orphaned and partial unit pairs remain selectable for teardown."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_known; do
        printf '  --%-15s Disable only the %s scraper\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

disable_task() {
    _dt_kind="$1"
    shift
    case "$_dt_kind" in
        success) _dt_marker='v'; _dt_color="$GREEN" ;;
        failure) _dt_marker='x'; _dt_color="$RED" ;;
        info) _dt_marker='i'; _dt_color="$CYAN" ;;
        warning) _dt_marker='!'; _dt_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _dt_prefix="    ${_dt_color}[${_dt_marker}]${NC} "
    _print_indented_wrapped "$_dt_prefix" '        ' "$@"
}

disable_finish() {
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
            disable_task failure "Unknown target '$_ssf_target'."
            if [ -n "${_st_known:-}" ]; then
                disable_task info \
                    "Available targets: $(stream_for_display "$_st_known")"
            else
                disable_task info \
                    "Run $(command_text './scrooge-alert disable --help') for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    disable_task failure "The installed target units could not be selected safely."
    disable_task info \
        "Run $(command_text './scrooge-alert disable --debug') for underlying diagnostics."
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
            disable_task info \
                "[$_sun_target] Target is registered but not installed; nothing to disable."
        fi
    done
    IFS="$_sun_old_ifs"
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
    section_heading success "Disable preflight"
    disable_task failure "The command-line arguments are invalid."
    disable_task info "Run $(command_text './scrooge-alert disable --help') for usage."
    disable_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Disable preflight"
    disable_task failure "systemctl (systemd) is not installed or not available."
    disable_task warning "Install systemd, then retry this command."
    disable_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    disable_load_catalog || true
fi
if ! run_action select_targets installed_union; then
    section_heading success "Disable preflight"
    show_selection_failure
    disable_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Background execution"
show_uninstalled_notices
if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        disable_task info "No installed target timer or service units found."
    disable_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    if run_action plugin_is_disabled "$plugin"; then
        disable_task info \
            "[$plugin] Background timer and service are already disabled."
        continue
    else
        state_status=$?
    fi
    if [ "$state_status" -eq 2 ]; then
        disable_task failure "[$plugin] Could not determine the systemd state."
        disable_task info \
            "[$plugin] Run $(command_text "./scrooge-alert disable --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    fi
    if run_with_progress \
        "[$plugin] Stopping and disabling background execution..." \
        run_action disable_one "$plugin"; then
        disable_task success "[$plugin] Background execution disabled."
    else
        disable_task failure \
            "[$plugin] Background execution was not fully disabled."
        disable_task info \
            "[$plugin] Run $(command_text "./scrooge-alert disable --debug --$plugin") for underlying diagnostics."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || disable_finish 1
printf '\n'
section_heading success "Optional controls"
disable_task info \
    "To re-enable background execution, run: $(command_text './scrooge-alert enable')"
disable_finish 0
