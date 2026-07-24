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
# registered scrapers and any still-installed services (known_targets) - a
# service left behind by a plugin removed upstream stays stoppable and so is
# listed.

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    load_plugin_manifest || true
    printf '\n'
    printf '%s\n' "Usage: stop.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Stop the currently running scraper service(s), aborting any in-progress"
    printf '%s\n' "scrape. With no target flag every running scraper service is stopped; pass"
    printf '%s\n' "one or more --<target> flags to stop only those."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _known="$(known_targets service)"
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for plugin in $_known; do
        printf '  --%-15s Stop only the %s scraper\n' "$plugin" "$plugin"
    done
    printf '\n'
}

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# With no flag, stop every *installed* scraper's running service - glob-derived,
# so it needs no venv and also catches an orphaned unit whose plugin was removed
# from the source tree. With one or more --<plugin> flags, stop just those.
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
    # Teardown acts on installed units. A name with an installed service (incl. an
    # orphan leftover) is stopped. A name that is only registered (no service on
    # disk) has nothing to stop - tell the user it is not installed instead of
    # acting as if there were a unit. A name in neither set is a typo: reject it.
    INSTALLED="$(list_installed_plugins service)"
    PLUGINS=""
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for sel in $SELECTED; do
        if stream_contains "$sel" "$INSTALLED"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        elif is_known_target "$sel" service; then
            printf "%b\n" "\n${YELLOW}[$sel] is registered but not installed - nothing to stop.${NC}"
            printf "%b\n" "Install it first with: ${CYAN}./install.sh --$sel${NC}"
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            _available="$(known_targets service)"
            printf "%b\n" "Available targets: ${CYAN}$(stream_for_display "$_available")${NC}"
            exit 1
        fi
    done
else
    PLUGINS="$(list_installed_plugins service)"
fi

if [ -z "$PLUGINS" ]; then
    # With explicit flags, any not-installed name was already reported per-plugin
    # above; only the no-flag "nothing installed at all" case needs this notice.
    [ -n "$SELECTED" ] || printf "%b\n" "\n${GREEN}No scraper services found. Nothing to stop.${NC}\n"
    exit 0
fi

# ------------------------------------------------------------------------------
# STOPPING SERVICE(S)
# ------------------------------------------------------------------------------
# For Type=oneshot services, the state is 'activating' while the script is running.

FAILED=0
# shellcheck disable=SC2086  # intentional newline-delimited target stream
for plugin in $PLUGINS; do
    if ! state="$(service_state "$plugin")"; then
        printf "%b\n" "\n${RED}[$plugin] Error: Could not determine whether the service is running.${NC}"
        FAILED=1
        continue
    fi
    if state_is_stopped "$state"; then
        printf "%b\n" "\n${GREEN}[$plugin] No active background execution detected. Nothing to stop.${NC}"
    else
        printf "%b\n" "\n${CYAN}[$plugin] Stopping active background execution...${NC}"
        if stop_one "$plugin"; then
            printf "%b\n" "${GREEN}[$plugin] Active background execution stopped successfully.${NC}"
        else
            printf "%b\n" "${RED}[$plugin] Error: Active background execution could not be stopped.${NC}"
            FAILED=1
        fi
    fi
done

if [ "$FAILED" -ne 0 ]; then
    printf "%b\n" "\n${RED}One or more scraper services could not be stopped.${NC}\n"
    exit 1
fi

printf "%b\n" "\nTo disable future background executions, run: ${CYAN}./scripts/disable.sh${NC}"
printf "%b\n" "To completely remove the application, run: ${CYAN}./scripts/uninstall.sh${NC}\n"
