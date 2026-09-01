#!/bin/sh
# Enable and start the background timers of installed, registered targets.
#
# Selection policy: installed_registered_timers, the intersection of installed
# timer units and registered plugins. Only a timer is enabled, so a leftover
# service without its timer is not selectable, and an orphan is refused rather
# than started: enabling a timer whose plugin is gone would schedule a run that
# cannot work. An unflagged run therefore skips orphans silently, while naming
# one explicitly fails with the uninstall remediation instead.

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
    _ph_installed="$(list_installed_units timer 2>/dev/null || true)"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = enable ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert enable [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: enable.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n' "Enable and start installed, registered scraper timers."
    printf '%s\n\n' "With no target flag, every eligible installed timer is enabled."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    for _ph_target in $_ph_installed; do
        stream_contains "$_ph_target" "$_ph_registered" || continue
        printf '  --%-15s Enable only the %s scraper\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

enable_finish() {
    end_operational_output
    exit "$1"
}

# The query runs in a command substitution, so systemd_property's own diagnostic
# would bypass run_action and reach the terminal in quiet mode. Redirect it there
# rather than reimplementing the property parse, so --debug still explains which
# property of which unit failed -- exactly as stop.sh reads the paired service.
capture_timer_property() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        systemd_property "$(unit_name "$1" timer)" "$2"
    else
        systemd_property "$(unit_name "$1" timer)" "$2" 2>/dev/null
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
    section_heading success "Enable preflight"
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert enable --help') for usage."
    enable_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Enable preflight"
    task_status failure "systemctl (systemd) is not installed or not available."
    task_status warning "Install systemd, then retry this command."
    enable_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    prime_plugin_catalog || true
fi
if ! run_action select_targets installed_registered_timers; then
    section_heading success "Enable preflight"
    show_timer_selection_failure enable
    enable_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Background schedules"
if [ -z "$PLUGINS" ]; then
    task_status info "No installed, registered target timers found."
    task_status warning "Run $(command_text './scrooge-alert install') to provision targets."
    enable_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
for plugin in $PLUGINS; do
    state_failed=0
    if timer_enabled="$(capture_timer_property "$plugin" UnitFileState)"; then
        :
    else
        timer_enabled=''
        state_failed=1
    fi
    if timer_active="$(capture_timer_property "$plugin" ActiveState)"; then
        :
    else
        timer_active=''
        state_failed=1
    fi
    if [ "$state_failed" -eq 1 ]; then
        task_status failure "[$plugin] Could not determine the timer state."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert enable --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    fi
    if [ "$timer_enabled" = enabled ] && [ "$timer_active" = active ]; then
        task_status info "[$plugin] Timer is already enabled and active."
        continue
    fi
    if run_with_progress "[$plugin] Enabling and starting the background schedule..." \
        run_action enable_one "$plugin"; then
        task_status success "[$plugin] Background schedule enabled and started."
    else
        task_status failure "[$plugin] Failed to enable and start the timer."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert enable --debug --$plugin") for underlying diagnostics."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || enable_finish 1
printf '\n'
section_heading success "Optional controls"
task_status info \
    "If needed, disable background execution with: $(command_text './scrooge-alert disable')"
enable_finish 0
