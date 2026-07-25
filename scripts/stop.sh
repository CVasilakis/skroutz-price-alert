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
    _ph_installed="$(list_installed_units service 2>/dev/null || true)"
    _ph_known="$(stream_union "$_ph_registered" "$_ph_installed")"
    printf '\n%s\n\n' "Usage: stop.sh [-h] [--<target> ...]"
    printf '%s\n\n' "Stop currently running installed scraper services."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_known; do
        printf '  --%-15s Stop only the %s scraper\n' "$_ph_target" "$_ph_target"
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
select_targets installed_services || exit 1
PLUGINS="$SELECTED_TARGETS"

if [ -z "$PLUGINS" ]; then
    [ "$TARGET_FLAGS_EXPLICIT" -eq 1 ] ||
        printf '\n%s\n\n' "No installed scraper services found. Nothing to stop."
    exit 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    if ! state="$(service_state "$plugin")"; then
        printf '\n%s\n' "[$plugin] Error: Could not determine service state."
        FAILED=1
    elif state_is_stopped "$state"; then
        printf '\n%s\n' "[$plugin] No active background execution detected."
    else
        printf '\n%s\n' "[$plugin] Stopping active background execution..."
        if stop_one "$plugin"; then
            printf '%s\n' "[$plugin] Active execution stopped successfully."
        else
            printf '%s\n' "[$plugin] Error: Active execution could not be stopped."
            FAILED=1
        fi
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || exit 1
printf '\n%s\n' "To disable future executions, run: ./scripts/disable.sh"
