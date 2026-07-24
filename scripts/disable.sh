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
    load_plugin_manifest || true
    printf '\n'
    printf '%s\n' "Usage: disable.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Stop and disable the background schedule (systemd timer) for the scraper(s)."
    printf '%s\n' "With no target flag every installed scraper's timer is disabled; pass one"
    printf '%s\n' "or more --<target> flags to disable only those."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _known="$(known_targets_all)"
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for plugin in $_known; do
        printf '  --%-15s Disable only the %s scraper\n' "$plugin" "$plugin"
    done
    printf '\n'
}

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# With no flag, disable every *installed* scraper's timer - glob-derived, so it
# needs no venv and also catches an orphaned unit whose plugin was removed from
# the source tree. With one or more --<plugin> flags, disable just those.
# -h/--help is honored anywhere in the argument list; a bare '--' is rejected
# (it would otherwise parse as an empty target name and silently select nothing).

SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help; exit 0 ;;
        --) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
        --*)
            target="${1#--}"
            require_valid_target "$target" || exit 1
            SELECTED="$(stream_add_unique "$SELECTED" "$target")"
            ;;
        *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
    esac
    shift
done

require_systemctl
load_plugin_manifest || true

if [ -n "$SELECTED" ]; then
    # Teardown acts on installed units. A name with an installed timer (incl. an
    # orphan leftover) is disabled. A name that is only registered (no timer on
    # disk) has nothing to disable - tell the user it is not installed instead of
    # acting as if there were a unit. A name in neither set is a typo: reject it.
    INSTALLED="$(list_installed_targets)"
    PLUGINS=""
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for sel in $SELECTED; do
        if stream_contains "$sel" "$INSTALLED"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        elif is_known_target_any "$sel"; then
            printf "%b\n" "\n${YELLOW}[$sel] is registered but not installed - nothing to disable.${NC}"
            printf "%b\n" "Install it first with: ${CYAN}./install.sh --$sel${NC}"
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            _available="$(known_targets_all)"
            printf "%b\n" "Available targets: ${CYAN}$(stream_for_display "$_available")${NC}"
            exit 1
        fi
    done
else
    PLUGINS="$(list_installed_targets)"
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

FAILED=0
# shellcheck disable=SC2086  # intentional newline-delimited target stream
for plugin in $PLUGINS; do
    if plugin_is_disabled "$plugin"; then
        printf "%b\n" "\n${GREEN}[$plugin] Background service and timer are already disabled. Nothing to do.${NC}"
        continue
    else
        disabled_state=$?
    fi
    if [ "$disabled_state" -eq 2 ]; then
        printf "%b\n" "\n${RED}[$plugin] Error: Could not determine the service and timer state.${NC}"
        FAILED=1
        continue
    fi

    printf "%b\n" "\n${CYAN}[$plugin] Stopping and disabling background schedule (timer)...${NC}"
    if disable_one "$plugin"; then
        printf "%b\n" "${GREEN}[$plugin] Background execution disabled successfully.${NC}"
    else
        printf "%b\n" "${RED}[$plugin] Error: Background execution was not fully disabled.${NC}"
        FAILED=1
    fi
done

if [ "$FAILED" -ne 0 ]; then
    printf "%b\n" "\n${RED}One or more background schedules could not be disabled.${NC}\n"
    exit 1
fi

printf "%b\n" "\nTo re-enable background execution, run: ${CYAN}./scripts/enable.sh${NC}"
printf "%b\n" "To completely remove the application, run: ${CYAN}./scripts/uninstall.sh${NC}\n"
