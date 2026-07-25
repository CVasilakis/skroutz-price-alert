#!/bin/sh
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
    printf '\n%s\n\n' "Usage: enable.sh [-h] [--<target> ...]"
    printf '%s\n' "Enable and start installed, registered scraper timers."
    printf '%s\n\n' "With no target flag, every eligible installed timer is enabled."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_installed; do
        stream_contains "$_ph_target" "$_ph_registered" || continue
        printf '  --%-15s Enable only the %s scraper\n' \
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
    printf '\n%s\n' "No installed, registered scraper timers found."
    printf '%s\n\n' "Run ./install.sh to provision scrapers."
    exit 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    if ! timer_enabled="$(timer_is_enabled "$plugin")" ||
        ! timer_active="$(timer_is_active "$plugin")"; then
        printf '\n%s\n' "[$plugin] Error: Could not determine the timer state."
        FAILED=1
        continue
    fi
    if [ "$timer_enabled" = enabled ] && [ "$timer_active" = active ]; then
        printf '\n%s\n' "[$plugin] Timer is already enabled and active."
        continue
    fi
    printf '\n%s\n' "[$plugin] Enabling and starting background schedule..."
    if enable_one "$plugin"; then
        printf '%s\n' "[$plugin] Background execution enabled successfully."
    else
        printf '%s\n' "[$plugin] Error: Failed to enable the timer."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || exit 1
printf '\n%s\n' "To disable background execution, run: ./scripts/disable.sh"
