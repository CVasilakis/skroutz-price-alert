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
    _ph_installed="$(list_installed_targets 2>/dev/null || true)"
    _ph_known="$(stream_union "$_ph_registered" "$_ph_installed")"
    printf '\n%s\n\n' "Usage: uninstall.sh [-h] [--debug] [--<target> ...]"
    printf '%s\n' "With no target, remove all installed units and the project venv."
    printf '%s\n\n' "With target flags, remove only those targets' unit entries."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_known; do
        printf '  --%-15s Remove only the %s target\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

uninstall_task() {
    _ut_kind="$1"
    shift
    case "$_ut_kind" in
        success) _ut_marker='v'; _ut_color="$GREEN" ;;
        failure) _ut_marker='x'; _ut_color="$RED" ;;
        info) _ut_marker='i'; _ut_color="$CYAN" ;;
        warning) _ut_marker='!'; _ut_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _ut_prefix="    ${_ut_color}[${_ut_marker}]${NC} "
    _print_indented_wrapped "$_ut_prefix" '        ' "$@"
}

uninstall_finish() {
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
            uninstall_task failure "Unknown target '$_ssf_target'."
            if [ -n "${_st_known:-}" ]; then
                uninstall_task info \
                    "Available targets: $(stream_for_display "$_st_known")"
            else
                uninstall_task info \
                    "Run ./scripts/uninstall.sh --help for available targets."
            fi
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    uninstall_task failure \
        "The installed target units could not be selected safely."
    uninstall_task info \
        "Run ./scripts/uninstall.sh --debug for underlying diagnostics."
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
            uninstall_task info \
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
    uninstall_task failure "The command-line arguments are invalid."
    uninstall_task info "Run ./scripts/uninstall.sh --help for usage."
    uninstall_finish 1
fi
if ! run_action reject_project_venv_symlink; then
    section_heading success "Uninstall preflight"
    uninstall_task failure \
        "$BASE_DIR/venv must be a project-owned directory, not a symlink."
    uninstall_task warning \
        "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or ./install.sh."
    uninstall_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    load_plugin_catalog || true
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
        uninstall_task failure \
            "systemctl (systemd) is not installed or not available."
        uninstall_task warning "Install systemd, then retry this command."
        uninstall_finish 1
    fi

    section_heading success "Background execution"
    TEARDOWN_FAILED=0
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for target in $REMOVE_TARGETS; do
        uninstall_task info \
            "[$target] Stopping and disabling the background timer and service."
        if run_action disable_one "$target"; then
            uninstall_task success \
                "[$target] Background timer and service disabled."
        else
            uninstall_task failure \
                "[$target] Background timer or service could not be disabled safely."
            uninstall_task info \
                "[$target] Run ./scripts/uninstall.sh --debug --$target for underlying diagnostics."
            TEARDOWN_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$TEARDOWN_FAILED" -ne 0 ]; then
        uninstall_task warning \
            "No unit entries were removed because every selected target must stop safely first."
        uninstall_finish 1
    fi

    printf '\n'
    section_heading success "Installed units"
    IFS='
'
    # rm -f unlinks symlinks themselves and never follows their targets.
    # shellcheck disable=SC2086
    for target in $REMOVE_TARGETS; do
        if ! run_action rm -f \
            "$SYSTEMD_USER_DIR/$(unit_name "$target" timer)" \
            "$SYSTEMD_USER_DIR/$(unit_name "$target" service)"; then
            IFS="$OLD_IFS"
            uninstall_task failure \
                "[$target] Timer and service unit entries could not be removed."
            uninstall_task info \
                "[$target] Run ./scripts/uninstall.sh --debug --$target for underlying diagnostics."
            uninstall_finish 1
        fi
        uninstall_task success \
            "[$target] Timer and service unit entries removed."
    done
    IFS="$OLD_IFS"
    if run_action systemctl --user daemon-reload; then
        uninstall_task success "systemd user manager reloaded."
    else
        uninstall_task failure "The systemd user manager could not be reloaded."
        uninstall_task warning \
            "Run systemctl --user daemon-reload after resolving the systemd failure."
        uninstall_finish 1
    fi
else
    section_heading success "Installed units"
    show_uninstalled_notices
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        uninstall_task info "No installed target timer or service units found."
fi

if [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ]; then
    printf '\n'
    section_heading success "Remaining installation"
    uninstall_task info \
        "The Python virtual environment and other targets were left intact."
    uninstall_finish 0
fi

printf '\n'
section_heading success "Python environment"
if [ -d "$BASE_DIR/venv" ]; then
    uninstall_task info "Removing the project Python virtual environment."
    if run_action rm -rf "${BASE_DIR:?}/venv"; then
        uninstall_task success "Python virtual environment removed."
    else
        uninstall_task failure "Python virtual environment could not be removed."
        uninstall_task info \
            "Run ./scripts/uninstall.sh --debug for underlying diagnostics."
        uninstall_finish 1
    fi
else
    uninstall_task info "Python virtual environment was already removed."
fi

printf '\n'
section_heading success "Uninstallation complete"
uninstall_task success "Configuration and state were preserved."
uninstall_finish 0
