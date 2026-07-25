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
    printf '\n%s\n\n' "Usage: disable.sh [-h] [--<target> ...]"
    printf '%s\n' "Stop and disable installed scraper timer/service pairs."
    printf '%s\n\n' "Orphaned and partial unit pairs remain selectable for teardown."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
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

parse_target_flags "$@" || exit 1
if [ "$TARGET_HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi
require_systemctl || exit 1
select_targets installed_union || exit 1
PLUGINS="$SELECTED_TARGETS"

if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        printf '\n%s\n\n' "No installed scraper units found. Nothing to do."
    exit 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    if plugin_is_disabled "$plugin"; then
        printf '\n%s\n' "[$plugin] Background service and timer are already disabled."
        continue
    else
        state_status=$?
    fi
    if [ "$state_status" -eq 2 ]; then
        printf '\n%s\n' "[$plugin] Error: Could not determine systemd state."
        FAILED=1
        continue
    fi
    printf '\n%s\n' "[$plugin] Stopping and disabling background execution..."
    if disable_one "$plugin"; then
        printf '%s\n' "[$plugin] Background execution disabled successfully."
    else
        printf '%s\n' "[$plugin] Error: Background execution was not fully disabled."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || exit 1
printf '\n%s\n' "To re-enable background execution, run: ./scripts/enable.sh"
