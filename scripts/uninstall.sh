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

VENV_DIR="venv"

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

print_help() {
    _registered="$(list_plugins 2>/dev/null || true)"

    # Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
    printf '\n'
    printf '%s\n' "Usage: uninstall.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "With no flag, performs a full teardown: removes every installed systemd"
    printf '%s\n' "timer/service and deletes the Python virtual environment (your .env and"
    printf '%s\n' "config/*.json are kept). With one or more --<target> flags, removes only"
    printf '%s\n' "those scrapers' units, leaving the virtual environment and other targets"
    printf '%s\n' "intact."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    for plugin in $_registered; do
        printf '  --%-15s Remove only the %s scraper\n' "$plugin" "$plugin"
    done

    # Leftover scrapers: a still-installed timer/service whose plugin is no longer
    # in the registry (removed or renamed upstream). They are not in the list
    # above but can still be purged by name, so surface them in their own section.
    _orphans=""
    for plugin in $(list_installed_plugins timer) $(list_installed_plugins service); do
        plugin_in_list "$plugin" $_registered && continue
        plugin_in_list "$plugin" $_orphans && continue
        _orphans="$_orphans $plugin"
    done

    if [ -n "$_orphans" ]; then
        printf '\n'
        printf '%s\n' "Leftover scrapers (no longer registered, units still installed):"
        for plugin in $_orphans; do
            printf '  --%-15s Remove the orphaned %s scraper units\n' "$plugin" "$plugin"
        done
    fi
    printf '\n'
}

# ------------------------------------------------------------------------------
# ARGUMENTS
# ------------------------------------------------------------------------------
# Usage:
#   ./scripts/uninstall.sh                  Full teardown (all units + venv).
#   ./scripts/uninstall.sh --<plugin> [..]  Remove only the named plugins' units (keep venv).
# -h/--help is honored anywhere in the argument list; a bare '--' is rejected
# (it would otherwise parse as an empty target name and silently select nothing).

SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help; exit 0 ;;
        --) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
        --*) SELECTED="$SELECTED ${1#--}" ;;
        *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
    esac
    shift
done

require_systemctl

# ------------------------------------------------------------------------------
# SELECTED-PLUGIN REMOVAL
# ------------------------------------------------------------------------------

if [ -n "$SELECTED" ]; then
    # Validate every name up front, so a typo in a later one doesn't leave the
    # earlier plugins half-removed. Removable if the plugin is registered OR has a
    # leftover timer/service unit (so orphans of a plugin deleted upstream can
    # still be purged). A name in none of those sets is a typo: reject it instead
    # of silently "succeeding" (rm -f on absent unit files would otherwise report
    # a misleading success).
    for sel in $SELECTED; do
        if ! is_known_target "$sel" timer && ! is_known_target "$sel" service; then
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            printf "%b\n" "Available targets: ${CYAN}$(printf '%s ' $(known_targets timer))${NC}"
            exit 1
        fi
    done

    for sel in $SELECTED; do
        printf "%b\n" "\n${CYAN}Removing systemd units for '$sel'...${NC}"
        disable_one "$sel"
        rm -f "$SYSTEMD_USER_DIR/$(unit_name "$sel" timer)"
        rm -f "$SYSTEMD_USER_DIR/$(unit_name "$sel" service)"
        printf "%b\n" "${GREEN}Removed '$sel' scraper units.${NC}"
    done
    systemctl --user daemon-reload

    printf "%b\n" "The virtual environment and any other targets were left intact.\n"
    exit 0
fi

# ------------------------------------------------------------------------------
# FULL SYSTEMD CLEANUP
# ------------------------------------------------------------------------------
# Glob every installed *-scraper.{timer,service}, so units are removed even for a
# plugin that was already deleted from the source tree. Timers and services are
# handled separately to also catch an orphaned half of a pair.

printf "%b\n" "\n${CYAN}Disabling and removing Systemd Timer(s) and Service(s)...${NC}"

for plugin in $(list_installed_plugins timer); do
    disable_one "$plugin"
    rm -f "$SYSTEMD_USER_DIR/$(unit_name "$plugin" timer)"
done

for plugin in $(list_installed_plugins service); do
    systemctl --user stop "$(unit_name "$plugin" service)" 2>/dev/null || true
    rm -f "$SYSTEMD_USER_DIR/$(unit_name "$plugin" service)"
done

# Reload systemd daemon to apply changes
systemctl --user daemon-reload

printf "%b\n" "${GREEN}Systemd configurations removed successfully.${NC}"

# ------------------------------------------------------------------------------
# PYTHON VIRTUAL ENVIRONMENT CLEANUP
# ------------------------------------------------------------------------------

printf "%b\n" "\n${CYAN}Removing Python virtual environment...${NC}"

if [ -d "$BASE_DIR/$VENV_DIR" ]; then
    # ${BASE_DIR:?} aborts if BASE_DIR is ever empty/unset, so this can never
    # expand to rm -rf "/<venv>".
    rm -rf "${BASE_DIR:?}/$VENV_DIR"
    printf "%b\n" "${GREEN}Python virtual environment ($VENV_DIR) removed.${NC}"
else
    printf "%b\n" "${GREEN}Python virtual environment ($VENV_DIR) already removed.${NC}"
fi

printf "%b\n" "\n${GREEN}Uninstallation complete!${NC}"
printf "%b\n" "\nUser configurations (.env, config/*.json) were NOT removed."
printf "%b\n" "User lingering (loginctl) was left enabled as other services might rely on it.\n"
printf "%b\n" "To re-install the application, run: ${CYAN}./install.sh${NC}"
printf "%b\n" "To completely purge everything, you can safely delete this folder.\n"
