#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
BASE_DIR="$( dirname "$SCRIPT_DIR" )"

# Shared helpers (colors, plugin enumeration, systemd helpers)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================
# Teardown acts on installed units, so the listed plugins are the union of the
# registered scrapers and any still-installed timers (known_targets) - a timer
# left behind by a plugin removed upstream stays disable-able and so is listed.

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    printf '\n'
    printf '%s\n' "Usage: disable.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Stop and disable the background schedule (systemd timer) for the scraper(s)."
    printf '%s\n' "With no target flag every installed scraper's timer is disabled; pass one"
    printf '%s\n' "or more --<target> flags to disable only those."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    for plugin in $(known_targets timer); do
        printf '  --%-15s Disable only the %s scraper\n' "$plugin" "$plugin"
    done
    printf '\n'
}

cd "$SCRIPT_DIR"

case "${1:-}" in
    -h|--help) print_help; exit 0 ;;
esac

require_systemctl

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# With no flag, disable every *installed* scraper's timer - glob-derived, so it
# needs no venv and also catches an orphaned unit whose plugin was removed from
# the source tree. With one or more --<plugin> flags, disable just those.

SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --*) SELECTED="$SELECTED ${1#--}" ;;
        *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
    esac
    shift
done

if [ -n "$SELECTED" ]; then
    # Teardown acts on installed units. A name with an installed timer (incl. an
    # orphan leftover) is disabled. A name that is only registered (no timer on
    # disk) has nothing to disable - tell the user it is not installed instead of
    # acting as if there were a unit. A name in neither set is a typo: reject it.
    INSTALLED="$(list_installed_plugins timer)"
    PLUGINS=""
    for sel in $SELECTED; do
        if plugin_in_list "$sel" $INSTALLED; then
            PLUGINS="$PLUGINS $sel"
        elif is_known_target "$sel" timer; then
            printf "%b\n" "\n${YELLOW}[$sel] is registered but not installed - nothing to disable.${NC}"
            printf "%b\n" "Install it first with: ${CYAN}./install.sh --$sel${NC}"
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            printf "%b\n" "Available targets: ${CYAN}$(printf '%s ' $(known_targets timer))${NC}"
            exit 1
        fi
    done
else
    PLUGINS="$(list_installed_plugins timer)"
fi

if [ -z "$PLUGINS" ]; then
    # With explicit flags, any not-installed name was already reported per-plugin
    # above; only the no-flag "nothing installed at all" case needs this notice.
    [ -n "$SELECTED" ] || printf "%b\n" "\n${GREEN}No scraper timers found. Nothing to do.${NC}\n"
    exit 0
fi

# ------------------------------------------------------------------------------
# DISABLING SERVICE(S)
# ------------------------------------------------------------------------------
# A plugin's service has no [Install] section (it is driven by its timer), so it
# is never "enabled". Work is only needed when the timer is enabled/active or the
# service is currently executing.

for plugin in $PLUGINS; do
    timer_enabled="$(timer_is_enabled "$plugin")"
    timer_active="$(timer_is_active "$plugin")"
    svc_state="$(service_state "$plugin")"

    if [ "$timer_enabled" != "enabled" ] && [ "$timer_active" != "active" ] && \
       [ "$svc_state" != "active" ] && [ "$svc_state" != "activating" ]; then
        printf "%b\n" "\n${GREEN}[$plugin] Background service and timer are already disabled. Nothing to do.${NC}"
        continue
    fi

    printf "%b\n" "\n${CYAN}[$plugin] Stopping and disabling background schedule (timer)...${NC}"
    disable_one "$plugin"
    printf "%b\n" "${GREEN}[$plugin] Background execution disabled successfully.${NC}"
done

printf "%b\n" "\nTo re-enable background execution, run: ${CYAN}./scripts/enable.sh${NC}"
printf "%b\n" "To completely remove the application, run: ${CYAN}./scripts/uninstall.sh${NC}\n"
