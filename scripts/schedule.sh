#!/bin/sh
# Apply each target's configured execution interval to its installed timer.
#
# Selection policy: installed_registered_timers, the same intersection enable.sh
# uses and for the same reason: re-rendering OnCalendar needs both the timer unit
# and the registered plugin that owns the configured or canonical interval, so an
# orphan has no interval to apply and is skipped rather than rescheduled.

set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"
# shellcheck source=scripts/lib/provisioning.sh
. "$SCRIPT_DIR/lib/provisioning.sh"

print_help() {
    load_plugin_catalog || true
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_units timer 2>/dev/null || true)"
    _ph_intervals="$(list_supported_intervals 2>/dev/null || true)"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = schedule ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert schedule [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: schedule.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n' "Apply configured execution intervals to installed target timers."
    printf '%s\n' "Only registered targets are eligible; orphaned timers are skipped."
    printf '%s\n\n' \
        "Supported intervals: ${_ph_intervals:-unavailable (run ./scrooge-alert install first)}"
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

schedule_finish() {
    end_operational_output
    exit "$1"
}

show_selection_failure() {
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

    _ssf_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ssf_target in $TARGET_FLAGS; do
        if stream_contains "$_ssf_target" "$SELECTED_INSTALLED"; then
            if ! stream_contains "$_ssf_target" "$SELECTED_REGISTERED"; then
                task_status failure \
                    "'$_ssf_target' is installed but no longer registered (orphan)."
                task_status warning \
                    "Remove it with: $(command_text "./scrooge-alert uninstall --$_ssf_target")"
                IFS="$_ssf_old_ifs"
                return
            fi
        elif stream_contains "$_ssf_target" "$SELECTED_REGISTERED"; then
            task_status failure \
                "'$_ssf_target' is registered but not installed."
            task_status warning "Install it with: $(command_text "./scrooge-alert install --$_ssf_target")"
            IFS="$_ssf_old_ifs"
            return
        else
            task_status failure "Unknown target '$_ssf_target'."
            task_status info \
                "Run $(command_text './scrooge-alert schedule --help') for available targets."
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    task_status failure "The installed target timers could not be selected safely."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
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
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert schedule --help') for usage."
    schedule_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Schedule preflight"
    task_status failure "systemctl (systemd) is not installed or not available."
    task_status warning "Install systemd, then retry this command."
    schedule_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    prime_plugin_catalog || true
fi
if ! run_action select_targets installed_registered_timers; then
    section_heading success "Schedule preflight"
    show_selection_failure
    schedule_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Execution intervals"
if [ -z "$PLUGINS" ]; then
    task_status info "No installed, registered target timers found."
    task_status warning "Run $(command_text './scrooge-alert install') to provision targets."
    schedule_finish 0
fi

if ! run_action prime_plugin_schedules ||
    ! run_action capture_schedule_value list_plugin_schedules; then
    task_status failure "Failed to resolve target scheduling metadata."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
    schedule_finish 1
fi
ALL_SCHEDULES="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_interval_status; then
    task_status failure "Failed to resolve target scheduling metadata."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
    schedule_finish 1
fi
INTERVAL_STATUS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_schedule_errors; then
    task_status failure "Failed to resolve target scheduling metadata."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
    schedule_finish 1
fi
SCHEDULE_ERRORS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value catalog_cli intervals; then
    task_status failure "Failed to resolve target scheduling metadata."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
    schedule_finish 1
fi
SUPPORTED_INTERVAL_KEYS="$SCHEDULE_VALUE"
if ! run_action capture_schedule_value list_plugin_examples; then
    task_status failure "Failed to resolve target scheduling metadata."
    task_status info \
        "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
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
        task_status failure "[$plugin] No interval status was returned."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert schedule --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    }
    case "$status" in
        error)
            schedule_error="$(
                plugin_stream_value "$plugin" "$SCHEDULE_ERRORS" || true
            )"
            task_status failure \
                "[$plugin] ${schedule_error:-Could not resolve its schedule.}"
            task_status info "[$plugin] Existing timer was left unchanged."
            CONFIG_FAILED=1
            continue ;;
        nocfg)
            task_status warning \
                "[$plugin] No config file found; timer left unchanged."
            if example_path="$(
                plugin_stream_value "$plugin" "$EXAMPLE_PAIRS"
            )"; then
                task_status warning \
                    "[$plugin] Copy $example_path to config/$plugin.json to configure it."
            fi
            continue ;;
        invalid)
            task_status warning \
                "[$plugin] Unsupported execution_interval; timer left unchanged."
            task_status warning \
                "[$plugin] Use one of: $SUPPORTED_INTERVAL_KEYS."
            continue ;;
    esac
    new_calendar="$(plugin_stream_value "$plugin" "$ALL_SCHEDULES")" || {
        task_status failure "[$plugin] No resolved schedule was returned."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert schedule --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    }
    if ! run_action capture_schedule_value read_timer_oncalendar "$plugin"; then
        task_status failure "[$plugin] Could not read the installed timer schedule."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert schedule --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    fi
    if [ "$new_calendar" = "$SCHEDULE_VALUE" ]; then
        task_status info \
            "[$plugin] Timer already matches the configured interval."
        continue
    fi
    task_status info "[$plugin] Timer schedule change queued."
    CHANGED="$(stream_add_unique "$CHANGED" "$plugin")"
    CHANGED_SCHEDULES="${CHANGED_SCHEDULES}${CHANGED_SCHEDULES:+
}${plugin}	${new_calendar}"
done
IFS="$OLD_IFS"

if [ -n "$CHANGED" ]; then
    printf '\n'
    section_heading success "Timer updates"
    if run_with_progress "Applying queued timer schedule changes..." \
        run_action schedule_units_transaction "$CHANGED" "$CHANGED_SCHEDULES"; then
        IFS='
'
        # shellcheck disable=SC2086
        for plugin in $CHANGED; do
            task_status success \
                "[$plugin] Timer updated and its previous state preserved."
        done
        IFS="$OLD_IFS"
    else
        task_status failure \
            "One or more timer schedules could not be applied."
        if [ -n "${UNIT_RECOVERY_DIR:-}" ]; then
            task_status warning "Rollback was incomplete. Recovery files:"
            task_status warning "$UNIT_RECOVERY_DIR"
        elif [ "${UNIT_MUTATION_STARTED:-0}" -eq 1 ]; then
            task_status info "Previous timer files and states were restored."
        else
            task_status info "No live timer file or state was changed."
        fi
        task_status info \
            "Run $(command_text './scrooge-alert schedule --debug') for underlying diagnostics."
        FAILED=1
    fi
fi

[ "$FAILED" -eq 0 ] || {
    schedule_finish 1
}
printf '\n'
section_heading success "Schedule result"
if [ -n "$CHANGED" ]; then
    task_status success "Updated targets: $(stream_for_display "$CHANGED")"
elif [ "$CONFIG_FAILED" -eq 0 ]; then
    task_status success "No eligible timer changes were required."
else
    task_status warning \
        "Invalid target configuration left the affected timer unchanged."
fi
[ "$CONFIG_FAILED" -eq 0 ] || schedule_finish "$EXIT_STATUS_TARGET_CONFIG_ERROR"
schedule_finish 0
