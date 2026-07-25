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

print_help() {
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_units timer 2>/dev/null || true)"
    _ph_intervals="$(list_supported_intervals 2>/dev/null || true)"
    printf '\n%s\n\n' "Usage: schedule.sh [-h] [--<target> ...]"
    printf '%s\n' "Apply configured execution intervals to installed scraper timers."
    printf '%s\n' "Only registered targets are eligible; orphaned timers are skipped."
    printf '%s\n\n' \
        "Supported intervals: ${_ph_intervals:-unavailable (run ./install.sh first)}"
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_installed; do
        stream_contains "$_ph_target" "$_ph_registered" || continue
        printf '  --%-15s Apply only the %s scraper interval\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

parse_target_flags "$@" || exit 1
if [ "$TARGET_HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi
require_systemctl || exit 1
select_targets installed_registered_timers || exit 1
PLUGINS="$SELECTED_TARGETS"

if [ -z "$PLUGINS" ]; then
    printf '\n%s\n\n' "No installed, registered scraper timers found."
    exit 0
fi

if ! load_plugin_schedules ||
    ! ALL_SCHEDULES="$(list_plugin_schedules)" ||
    ! INTERVAL_STATUS="$(list_interval_status)" ||
    ! SCHEDULE_ERRORS="$(list_schedule_errors)" ||
    ! SUPPORTED_INTERVAL_KEYS="$(list_supported_intervals)" ||
    ! EXAMPLE_PAIRS="$(list_plugin_examples)"; then
    printf '%s\n' "Error: Failed to resolve scraper scheduling metadata." >&2
    exit 1
fi

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
        printf '%s\n' "[$plugin] Error: No interval status was returned." >&2
        FAILED=1
        continue
    }
    case "$status" in
        error)
            schedule_error="$(
                plugin_stream_value "$plugin" "$SCHEDULE_ERRORS" || true
            )"
            printf '\n%s\n' \
                "[$plugin] Error: ${schedule_error:-Could not resolve its schedule.}"
            printf '%s\n' "[$plugin] Existing timer was left unchanged."
            CONFIG_FAILED=1
            continue ;;
        nocfg)
            printf '\n%s\n' "[$plugin] No config file found; timer left unchanged."
            if example_path="$(
                plugin_stream_value "$plugin" "$EXAMPLE_PAIRS"
            )"; then
                printf '%s\n' \
                    "Copy $example_path to config/$plugin.json to configure it."
            fi
            continue ;;
        invalid)
            printf '\n%s\n' \
                "[$plugin] Unsupported execution_interval; timer left unchanged."
            printf '%s\n' "Use one of: $SUPPORTED_INTERVAL_KEYS."
            continue ;;
    esac
    new_calendar="$(plugin_stream_value "$plugin" "$ALL_SCHEDULES")" || {
        FAILED=1
        continue
    }
    if [ "$new_calendar" = "$(read_timer_oncalendar "$plugin")" ]; then
        printf '\n%s\n' "[$plugin] Timer already matches the configured interval."
        continue
    fi
    printf '\n%s\n' "[$plugin] Updating the timer schedule..."
    CHANGED="$(stream_add_unique "$CHANGED" "$plugin")"
    CHANGED_SCHEDULES="${CHANGED_SCHEDULES}${CHANGED_SCHEDULES:+
}${plugin}	${new_calendar}"
done
IFS="$OLD_IFS"

if [ -n "$CHANGED" ]; then
    if schedule_units_transaction "$CHANGED" "$CHANGED_SCHEDULES"; then
        IFS='
'
        # shellcheck disable=SC2086
        for plugin in $CHANGED; do
            printf '%s\n' "[$plugin] Timer unit updated."
        done
        IFS="$OLD_IFS"
    else
        FAILED=1
    fi
fi

[ "$FAILED" -eq 0 ] || {
    printf '%s\n' "Error: One or more timer schedules could not be applied." >&2
    exit 1
}
if [ -n "$CHANGED" ]; then
    printf '\n%s\n' "Updated: $(stream_for_display "$CHANGED")"
elif [ "$CONFIG_FAILED" -eq 0 ]; then
    printf '\n%s\n' "All timers already match their configured intervals."
fi
[ "$CONFIG_FAILED" -eq 0 ] || exit 15
