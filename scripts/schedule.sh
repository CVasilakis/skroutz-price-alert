#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"
# shellcheck source=scripts/lib/provisioning.sh
. "$SCRIPT_DIR/lib/provisioning.sh"

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

# shellcheck disable=SC2034,SC2329  # consumed indirectly through shared helpers
load_plugin_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if [ "$DEBUG_MODE" -eq 1 ]; then
        if run_captured catalog_cli schedules --config-dir "$BASE_DIR/config"; then
            PLUGIN_SCHEDULE_DATA="$CAPTURED_COMMAND_OUTPUT"
            PLUGIN_SCHEDULE_STATE=1
            return 0
        fi
    elif PLUGIN_SCHEDULE_DATA="$(
        catalog_cli schedules --config-dir "$BASE_DIR/config" 2>/dev/null
    )"; then
        PLUGIN_SCHEDULE_STATE=1
        return 0
    fi
    PLUGIN_SCHEDULE_STATE=2
    PLUGIN_SCHEDULE_DATA=''
    return 1
}

print_help() {
    load_plugin_catalog || true
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_units timer 2>/dev/null || true)"
    _ph_intervals="$(list_supported_intervals 2>/dev/null || true)"
    printf '\n%s\n\n' "Usage: schedule.sh [-h] [--debug] [--<target> ...]"
    printf '%s\n' "Apply configured execution intervals to installed target timers."
    printf '%s\n' "Only registered targets are eligible; orphaned timers are skipped."
    printf '%s\n\n' \
        "Supported intervals: ${_ph_intervals:-unavailable (run ./install.sh first)}"
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_installed; do
        stream_contains "$_ph_target" "$_ph_registered" || continue
        printf '  --%-15s Apply only the %s target interval\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

schedule_task() {
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

schedule_finish() {
    end_operational_output
    exit "$1"
}

show_selection_failure() {
    if [ -n "${_st_installed:-}" ] && [ -z "${_st_registered:-}" ]; then
        schedule_task failure "The target catalog could not be loaded."
        if [ ! -x "$BASE_DIR/venv/bin/python3" ] || [ -L "$BASE_DIR/venv" ]; then
            schedule_task warning \
                "Reinstall it with: ./scripts/uninstall.sh then ./install.sh"
        else
            schedule_task warning \
                "Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry."
        fi
        return
    fi

    _ssf_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ssf_target in $TARGET_FLAGS; do
        if stream_contains "$_ssf_target" "${_st_installed:-}"; then
            if ! stream_contains "$_ssf_target" "${_st_registered:-}"; then
                schedule_task failure \
                    "'$_ssf_target' is installed but no longer registered (orphan)."
                schedule_task warning \
                    "Remove it with: ./scripts/uninstall.sh --$_ssf_target"
                IFS="$_ssf_old_ifs"
                return
            fi
        elif stream_contains "$_ssf_target" "${_st_registered:-}"; then
            schedule_task failure \
                "'$_ssf_target' is registered but not installed."
            schedule_task warning "Install it with: ./install.sh --$_ssf_target"
            IFS="$_ssf_old_ifs"
            return
        else
            schedule_task failure "Unknown target '$_ssf_target'."
            schedule_task info \
                "Run ./scripts/schedule.sh --help for available targets."
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    schedule_task failure "The installed target timers could not be selected safely."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
}

# shellcheck disable=SC2329  # invoked indirectly through run_action
capture_schedule_value() {
    if run_captured "$@"; then
        SCHEDULE_VALUE="$CAPTURED_COMMAND_OUTPUT"
        return 0
    fi
    SCHEDULE_VALUE=''
    return 1
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
    section_heading success "Schedule preflight"
    schedule_task failure "The command-line arguments are invalid."
    schedule_task info "Run ./scripts/schedule.sh --help for usage."
    schedule_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Schedule preflight"
    schedule_task failure "systemctl (systemd) is not installed or not available."
    schedule_task warning "Install systemd, then retry this command."
    schedule_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    load_plugin_catalog || true
fi
if ! run_action select_targets installed_registered_timers; then
    section_heading success "Schedule preflight"
    show_selection_failure
    schedule_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Execution intervals"
if [ -z "$PLUGINS" ]; then
    schedule_task info "No installed, registered target timers found."
    schedule_task warning "Run ./install.sh to provision targets."
    schedule_finish 0
fi

if ! run_action load_plugin_schedules ||
    ! run_action capture_schedule_value list_plugin_schedules; then
    schedule_task failure "Failed to resolve target scheduling metadata."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
    schedule_finish 1
fi
ALL_SCHEDULES="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_interval_status; then
    schedule_task failure "Failed to resolve target scheduling metadata."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
    schedule_finish 1
fi
INTERVAL_STATUS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_schedule_errors; then
    schedule_task failure "Failed to resolve target scheduling metadata."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
    schedule_finish 1
fi
SCHEDULE_ERRORS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value catalog_cli intervals; then
    schedule_task failure "Failed to resolve target scheduling metadata."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
    schedule_finish 1
fi
SUPPORTED_INTERVAL_KEYS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_plugin_examples; then
    schedule_task failure "Failed to resolve target scheduling metadata."
    schedule_task info \
        "Run ./scripts/schedule.sh --debug for underlying diagnostics."
    schedule_finish 1
fi
EXAMPLE_PAIRS="$SCHEDULE_VALUE"

CHANGED=''
CHANGED_SCHEDULES=''
FAILED=0
CONFIG_FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    status="$(plugin_stream_value "$plugin" "$INTERVAL_STATUS")" || {
        schedule_task failure "[$plugin] No interval status was returned."
        schedule_task info \
            "[$plugin] Run ./scripts/schedule.sh --debug --$plugin for underlying diagnostics."
        FAILED=1
        continue
    }
    case "$status" in
        error)
            schedule_error="$(
                plugin_stream_value "$plugin" "$SCHEDULE_ERRORS" || true
            )"
            schedule_task failure \
                "[$plugin] ${schedule_error:-Could not resolve its schedule.}"
            schedule_task info "[$plugin] Existing timer was left unchanged."
            CONFIG_FAILED=1
            continue ;;
        nocfg)
            schedule_task warning \
                "[$plugin] No config file found; timer left unchanged."
            if example_path="$(
                plugin_stream_value "$plugin" "$EXAMPLE_PAIRS"
            )"; then
                schedule_task warning \
                    "[$plugin] Copy $example_path to config/$plugin.json to configure it."
            fi
            continue ;;
        invalid)
            schedule_task warning \
                "[$plugin] Unsupported execution_interval; timer left unchanged."
            schedule_task warning \
                "[$plugin] Use one of: $SUPPORTED_INTERVAL_KEYS."
            continue ;;
    esac
    new_calendar="$(plugin_stream_value "$plugin" "$ALL_SCHEDULES")" || {
        schedule_task failure "[$plugin] No resolved schedule was returned."
        schedule_task info \
            "[$plugin] Run ./scripts/schedule.sh --debug --$plugin for underlying diagnostics."
        FAILED=1
        continue
    }
    if ! run_action capture_schedule_value read_timer_oncalendar "$plugin"; then
        schedule_task failure "[$plugin] Could not read the installed timer schedule."
        schedule_task info \
            "[$plugin] Run ./scripts/schedule.sh --debug --$plugin for underlying diagnostics."
        FAILED=1
        continue
    fi
    if [ "$new_calendar" = "$SCHEDULE_VALUE" ]; then
        schedule_task info \
            "[$plugin] Timer already matches the configured interval."
        continue
    fi
    schedule_task info "[$plugin] Timer schedule change queued."
    CHANGED="$(stream_add_unique "$CHANGED" "$plugin")"
    CHANGED_SCHEDULES="${CHANGED_SCHEDULES}${CHANGED_SCHEDULES:+
}${plugin}	${new_calendar}"
done
IFS="$OLD_IFS"

if [ -n "$CHANGED" ]; then
    printf '\n'
    section_heading success "Timer updates"
    if run_action schedule_units_transaction "$CHANGED" "$CHANGED_SCHEDULES"; then
        IFS='
'
        # shellcheck disable=SC2086
        for plugin in $CHANGED; do
            schedule_task success \
                "[$plugin] Timer updated and its previous state preserved."
        done
        IFS="$OLD_IFS"
    else
        schedule_task failure \
            "One or more timer schedules could not be applied."
        if [ -n "${UNIT_RECOVERY_DIR:-}" ]; then
            schedule_task warning "Rollback was incomplete. Recovery files:"
            schedule_task warning "$UNIT_RECOVERY_DIR"
        elif [ "${UNIT_MUTATION_STARTED:-0}" -eq 1 ]; then
            schedule_task info "Previous timer files and states were restored."
        else
            schedule_task info "No live timer file or state was changed."
        fi
        schedule_task info \
            "Run ./scripts/schedule.sh --debug for underlying diagnostics."
        FAILED=1
    fi
fi

[ "$FAILED" -eq 0 ] || {
    schedule_finish 1
}
printf '\n'
section_heading success "Schedule result"
if [ -n "$CHANGED" ]; then
    schedule_task success "Updated targets: $(stream_for_display "$CHANGED")"
elif [ "$CONFIG_FAILED" -eq 0 ]; then
    schedule_task success "No eligible timer changes were required."
else
    schedule_task warning \
        "Invalid target configuration left the affected timer unchanged."
fi
[ "$CONFIG_FAILED" -eq 0 ] || schedule_finish 15
schedule_finish 0
