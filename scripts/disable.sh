#!/bin/sh
# Stop and disable the background timer/service pairs of installed targets.
#
# Selection policy: installed_union, every installed timer or service unit
# whether or not its plugin is still registered. Disabling touches both units of
# a pair and is pure teardown, so orphans and half-installed pairs must stay
# reachable; requiring registration here would strand exactly the units a user
# most needs to switch off.

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
    for _ph_target in $_ph_known; do
        printf '  --%-15s Disable only the %s scraper\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

disable_finish() {
    end_operational_output
    exit "$1"
}

# Quiet-mode rendering of a failed `select_targets installed_union`; see that
# helper in common.sh for why the teardown scripts each keep their own copy
# rather than sharing one. stop.sh and uninstall.sh hold the same shape, in
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
                task_status info \
                    "Run $(command_text './scrooge-alert disable --help') for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    task_status failure "The installed target units could not be selected safely."
    task_status info \
        "Run $(command_text './scrooge-alert disable --debug') for underlying diagnostics."
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
    section_heading success "Disable preflight"
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert disable --help') for usage."
    disable_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Disable preflight"
    task_status failure "systemctl (systemd) is not installed or not available."
    task_status warning "Install systemd, then retry this command."
    disable_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    prime_plugin_catalog || true
fi
if ! run_action select_targets installed_union; then
    section_heading success "Disable preflight"
    show_selection_failure
    disable_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Background execution"
show_uninstalled_notices disable
if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        task_status info "No installed target timer or service units found."
    disable_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
for plugin in $PLUGINS; do
    if run_action plugin_is_disabled "$plugin"; then
        task_status info \
            "[$plugin] Background timer and service are already disabled."
        continue
    else
        state_status=$?
    fi
    if [ "$state_status" -eq 2 ]; then
        task_status failure "[$plugin] Could not determine the systemd state."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert disable --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    fi
    if run_with_progress \
        "[$plugin] Stopping and disabling background execution..." \
        run_action disable_one "$plugin"; then
        task_status success "[$plugin] Background execution disabled."
    else
        task_status failure \
            "[$plugin] Background execution was not fully disabled."
        task_status info \
            "[$plugin] Run $(command_text "./scrooge-alert disable --debug --$plugin") for underlying diagnostics."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || disable_finish 1
printf '\n'
section_heading success "Optional controls"
task_status info \
    "To re-enable background execution, run: $(command_text './scrooge-alert enable')"
disable_finish 0
