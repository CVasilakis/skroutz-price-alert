#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"

print_help() {
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_targets 2>/dev/null || true)"
    _ph_known="$(stream_union "$_ph_registered" "$_ph_installed")"
    printf '\n%s\n\n' "Usage: uninstall.sh [-h] [--<target> ...]"
    printf '%s\n' "With no target, remove all installed units and the project venv."
    printf '%s\n\n' "With target flags, remove only those targets' unit entries."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_known; do
        printf '  --%-15s Remove only the %s scraper\n' \
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
reject_project_venv_symlink || exit 1
select_targets installed_union || exit 1
REMOVE_TARGETS="$SELECTED_TARGETS"

# A unitless full uninstall does not need a running systemd user manager.
if [ -n "$REMOVE_TARGETS" ]; then
    require_systemctl || exit 1
    TEARDOWN_FAILED=0
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for target in $REMOVE_TARGETS; do
        printf '\n%s\n' "Stopping and disabling '$target'..."
        disable_one "$target" || TEARDOWN_FAILED=1
    done
    IFS="$OLD_IFS"
    if [ "$TEARDOWN_FAILED" -ne 0 ]; then
        printf '%s\n' "Error: No unit entries were removed." >&2
        exit 1
    fi

    IFS='
'
    # rm -f unlinks symlinks themselves and never follows their targets.
    # shellcheck disable=SC2086
    for target in $REMOVE_TARGETS; do
        rm -f "$SYSTEMD_USER_DIR/$(unit_name "$target" timer)" \
            "$SYSTEMD_USER_DIR/$(unit_name "$target" service)" || {
            IFS="$OLD_IFS"
            printf '%s\n' "Error: Failed to remove '$target' unit entries." >&2
            exit 1
        }
        printf '%s\n' "Removed '$target' scraper units."
    done
    IFS="$OLD_IFS"
    systemctl --user daemon-reload || {
        printf '%s\n' "Error: Failed to reload the systemd user manager." >&2
        exit 1
    }
fi

if [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ]; then
    [ -n "$REMOVE_TARGETS" ] ||
        printf '%s\n' "No selected target had installed units."
    printf '%s\n' "The virtual environment and other targets were left intact."
    exit 0
fi

printf '\n%s\n' "Removing Python virtual environment..."
if [ -d "$BASE_DIR/venv" ]; then
    rm -rf "${BASE_DIR:?}/venv"
    printf '%s\n' "Python virtual environment removed."
else
    printf '%s\n' "Python virtual environment already removed."
fi
printf '\n%s\n' "Uninstallation complete. Configuration and state were preserved."
