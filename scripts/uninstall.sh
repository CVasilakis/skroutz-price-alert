#!/bin/sh
# Remove installed unit entries, and on a full teardown the project venv.
#
# Selection policy: installed_union, the same teardown-reaches-everything rule
# disable.sh uses, since an orphaned or half-installed pair is precisely what an
# uninstall has to be able to remove.

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
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = uninstall ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert uninstall [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: uninstall.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n' "With no target, remove all installed units and the project venv."
    printf '%s\n\n' "With target flags, remove only those targets' unit entries."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    for _ph_target in $_ph_known; do
        printf '  --%-15s Remove only the %s target\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

uninstall_finish() {
    end_operational_output
    exit "$1"
}

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
                    "Run $(command_text './scrooge-alert uninstall --help') for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    task_status failure \
        "The installed target units could not be selected safely."
    task_status info \
        "Run $(command_text './scrooge-alert uninstall --debug') for underlying diagnostics."
}

show_uninstalled_notices() {
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] || return 0
    _sun_old_ifs="$IFS"
    IFS='
'
    for _sun_target in $TARGET_FLAGS; do
        if stream_contains "$_sun_target" "$SELECTED_REGISTERED" &&
            ! stream_contains "$_sun_target" "$SELECTED_INSTALLED"; then
            task_status info \
                "[$_sun_target] Target is registered but not installed; nothing to remove."
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
    section_heading success "Uninstall preflight"
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert uninstall --help') for usage."
    uninstall_finish 1
fi
if ! run_action reject_project_venv_symlink; then
    section_heading success "Uninstall preflight"
    task_status failure \
        "$BASE_DIR/venv must be a project-owned directory, not a symlink."
    task_status warning \
        "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')."
    uninstall_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    prime_plugin_catalog || true
fi
if ! run_action select_targets installed_union; then
    section_heading success "Uninstall preflight"
    show_selection_failure
    uninstall_finish 1
fi
REMOVE_TARGETS="$SELECTED_TARGETS"

# A unitless full uninstall does not need a running systemd user manager.
if [ -n "$REMOVE_TARGETS" ]; then
    if ! run_action require_systemctl; then
        section_heading success "Uninstall preflight"
        task_status failure \
            "systemctl (systemd) is not installed or not available."
        task_status warning "Install systemd, then retry this command."
        uninstall_finish 1
    fi

    section_heading success "Background execution"
    TEARDOWN_FAILED=0
    OLD_IFS="$IFS"
    IFS='
'
    for target in $REMOVE_TARGETS; do
        if run_with_progress \
            "[$target] Stopping and disabling the background timer and service..." \
            run_action disable_one "$target"; then
            task_status success \
                "[$target] Background timer and service disabled."
        else
            task_status failure \
                "[$target] Background timer or service could not be disabled safely."
            task_status info \
                "[$target] Run $(command_text "./scrooge-alert uninstall --debug --$target") for underlying diagnostics."
            TEARDOWN_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$TEARDOWN_FAILED" -ne 0 ]; then
        task_status warning \
            "No unit entries were removed because every selected target must stop safely first."
        uninstall_finish 1
    fi

    printf '\n'
    section_heading success "Installed units"
    IFS='
'
    # rm -f unlinks symlinks themselves and never follows their targets.
    for target in $REMOVE_TARGETS; do
        if ! run_action rm -f \
            "$SYSTEMD_USER_DIR/$(unit_name "$target" timer)" \
            "$SYSTEMD_USER_DIR/$(unit_name "$target" service)"; then
            IFS="$OLD_IFS"
            task_status failure \
                "[$target] Timer and service unit entries could not be removed."
            task_status info \
                "[$target] Run $(command_text "./scrooge-alert uninstall --debug --$target") for underlying diagnostics."
            uninstall_finish 1
        fi
        task_status success \
            "[$target] Timer and service unit entries removed."
    done
    IFS="$OLD_IFS"
    if run_action systemctl --user daemon-reload; then
        task_status success "systemd user manager reloaded."
    else
        task_status failure "The systemd user manager could not be reloaded."
        task_status warning \
            "Run systemctl --user daemon-reload after resolving the systemd failure."
        uninstall_finish 1
    fi
else
    section_heading success "Installed units"
    show_uninstalled_notices
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        task_status info "No installed target timer or service units found."
fi

if [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ]; then
    printf '\n'
    section_heading success "Remaining installation"
    task_status info \
        "The Python virtual environment and other targets were left intact."
    uninstall_finish 0
fi

printf '\n'
section_heading success "Python environment"
if [ -d "$BASE_DIR/venv" ]; then
    if run_with_progress "Removing the project Python virtual environment..." \
        run_action rm -rf "${BASE_DIR:?}/venv"; then
        task_status success "Python virtual environment removed."
    else
        task_status failure "Python virtual environment could not be removed."
        task_status info \
            "Run $(command_text './scrooge-alert uninstall --debug') for underlying diagnostics."
        uninstall_finish 1
    fi
else
    task_status info "Python virtual environment was already removed."
fi

printf '\n'
section_heading success "Uninstallation complete"
task_status success "Configuration and state were preserved."
uninstall_finish 0
