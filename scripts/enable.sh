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

print_help() {
    _registered="$(list_plugins 2>/dev/null || true)"

    # Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
    printf '\n'
    printf '%s\n' "Usage: enable.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Enable and start the background schedule (systemd timer) for the installed"
    printf '%s\n' "scraper(s). With no target flag every installed scraper's timer is enabled;"
    printf '%s\n' "pass one or more --<target> flags to enable only those."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    for plugin in $(list_installed_plugins timer); do
        # Skip orphans (installed but no longer a registered scraper) - they can't
        # be enabled. If the registry is unreadable we can't tell, so list them all.
        if [ -n "$_registered" ] && ! plugin_in_list "$plugin" $_registered; then
            continue
        fi
        printf '  --%-15s Enable only the %s scraper\n' "$plugin" "$plugin"
    done
    printf '\n'
}

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# enable.sh re-arms the timers that install.sh provisioned, so it acts on the
# INSTALLED timer units (glob-derived) - never the bare registry, so a selective
# install (e.g. ./install.sh --skroutz) is preserved. It then intersects with the
# registry to drop any *orphan*: a unit still on disk whose plugin was removed from
# the project (so it is no longer registered). Re-arming one would only schedule a
# job whose code is gone. Because that orphan check needs the registry, a readable
# registry is REQUIRED: if units exist but it can't be read the Python environment
# is broken (and broken scrapers cannot run anyway), so enable refuses with a
# repair hint rather than arming timers that would only fail on schedule.
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

INSTALLED_PLUGINS="$(list_installed_plugins timer)"
REGISTERED="$(list_plugins 2>/dev/null || true)"

# Units exist but the registry can't be read -> refuse rather than guess: arming a
# timer we cannot vet (orphan or not) just schedules a job that cannot run.
# registry_diagnose says WHY (venv missing vs. a plugin whose discovery failed).
if [ -n "$INSTALLED_PLUGINS" ] && [ -z "$REGISTERED" ]; then
    registry_diagnose || exit 1
fi

if [ -n "$SELECTED" ]; then
    PLUGINS=""
    for sel in $SELECTED; do
        if plugin_in_list "$sel" $INSTALLED_PLUGINS; then
            # Installed: arm it - unless the registry omits it, i.e. it is an orphan
            # whose code is gone, in which case point at uninstall instead. (The
            # guard above guarantees the registry is readable when units exist.)
            if ! plugin_in_list "$sel" $REGISTERED; then
                printf "%b\n" "${RED}Error: '$sel' is installed but no longer a registered scraper (orphan).${NC}"
                printf "%b\n" "Remove its leftover units with: ${CYAN}./scripts/uninstall.sh --$sel${NC}"
                exit 1
            fi
            PLUGINS="$PLUGINS $sel"
        elif plugin_in_list "$sel" $REGISTERED; then
            # A real scraper, but install.sh never provisioned its timer - enable
            # cannot arm a unit that does not exist.
            printf "%b\n" "${RED}Error: '$sel' is registered but not installed.${NC}"
            printf "%b\n" "Install it first with: ${CYAN}./install.sh --$sel${NC}"
            exit 1
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            [ -z "$INSTALLED_PLUGINS" ] || \
                printf "%b\n" "Installed scrapers: ${CYAN}$(printf '%s ' $INSTALLED_PLUGINS)${NC}"
            exit 1
        fi
    done
else
    # No flag: enable every installed timer that is STILL a registered scraper,
    # i.e. installed ∩ registry - skipping orphans whose code was removed.
    PLUGINS=""
    for plugin in $INSTALLED_PLUGINS; do
        plugin_in_list "$plugin" $REGISTERED && PLUGINS="$PLUGINS $plugin"
    done
fi

if [ -z "$PLUGINS" ]; then
    if [ -n "$INSTALLED_PLUGINS" ]; then
        # Units exist but none survived the registry intersection: every installed
        # unit is an orphan (its plugin was removed from the project).
        printf "%b\n" "\n${YELLOW}Nothing to enable: every installed unit is an orphan (no longer a registered scraper).${NC}"
        printf "%b\n" "Remove the leftovers with ${CYAN}./scripts/uninstall.sh${NC} (see ${CYAN}./scripts/uninstall.sh --help${NC})."
    else
        printf "%b\n" "\n${YELLOW}No installed scrapers found.${NC}"
        printf "%b\n" "Run ${CYAN}./install.sh${NC} to provision your scrapers.\n"
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# ENABLING SERVICE(S)
# ------------------------------------------------------------------------------

for plugin in $PLUGINS; do
    if [ "$(timer_is_enabled "$plugin")" = "enabled" ] && [ "$(timer_is_active "$plugin")" = "active" ]; then
        printf "%b\n" "\n${GREEN}[$plugin] Timer is already enabled and active. Nothing to do.${NC}"
        continue
    fi

    printf "%b\n" "\n${CYAN}[$plugin] Enabling and starting background schedule (timer)...${NC}"
    if enable_one "$plugin"; then
        printf "%b\n" "${GREEN}[$plugin] Background execution enabled successfully.${NC}"
    else
        printf "%b\n" "${RED}[$plugin] Error: Failed to enable the timer!${NC}"
        printf "%b\n" "${RED}Try running ./install.sh to fix the issue.${NC}\n"
        exit 1
    fi
done

printf "%b\n" "\nTo disable background execution, run: ${CYAN}./scripts/disable.sh${NC}"
printf "%b\n" "To completely remove the application, run: ${CYAN}./scripts/uninstall.sh${NC}\n"
