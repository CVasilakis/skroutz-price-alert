#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

# Automatically get the directory where the script is located (repository root)
SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
BASE_DIR="$SCRIPT_DIR"

# Shared helpers (colors, plugin enumeration, systemd helpers)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/scripts/lib/preflight.sh"
# shellcheck source=scripts/lib/provisioning.sh
. "$SCRIPT_DIR/scripts/lib/provisioning.sh"

# Environment and File Configurations
VENV_DIR="venv"
REQUIREMENTS_FILE="requirements.txt"

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    load_plugin_manifest || true
    printf '\n'
    printf '%s\n' "Usage: install.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Set up the Python virtual environment and install the systemd timer(s) and"
    printf '%s\n' "service(s). With no target flag every registered scraper is installed and"
    printf '%s\n' "enabled; pass one or more --<target> flags to install only those. You can"
    printf '%s\n' "run this command as many times as you like - run it again in the future"
    printf '%s\n' "to install additional scrapers."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    for plugin in $(list_plugins 2>/dev/null || true); do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Install and enable only the %s scraper\n' "$plugin" "${display_name:-$plugin}"
    done
    printf '\n'
}

# ==============================================================================
# ARGUMENTS
# ==============================================================================
# Usage:
#   ./install.sh                        Install everything and provision every plugin.
#   ./install.sh --<plugin> [...]       (Re)provision and enable only the named plugin(s).
#   ./install.sh --update [<plugin>..]  Invoked by update.sh (quiet banner). Reprovisions
#                                       only the named plugins (the set update.sh derived
#                                       from the already-installed units), or every plugin
#                                       when none are named. Plugins that no longer exist in
#                                       the catalog (removed/renamed in the new version) are
#                                       skipped instead of aborting the update.

INSTALL_MODE="all"   # all | selected
IS_UPDATE=0
UPDATE_FLAG_COUNT=0
SELECTED=""

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --update)
            IS_UPDATE=1
            UPDATE_FLAG_COUNT=$((UPDATE_FLAG_COUNT + 1))
            ;;
        --)
            # A bare '--' would otherwise parse as an empty target name and
            # silently select nothing.
            printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"
            exit 1
            ;;
        --*)
            INSTALL_MODE="selected"
            target="${1#--}"
            require_valid_target "$target" || exit 1
            SELECTED="$(stream_add_unique "$SELECTED" "$target")"
            ;;
        *)
            printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"
            exit 1
            ;;
    esac
    shift
done

if [ "$IS_UPDATE" -eq 1 ]; then
    if [ "$UPDATE_FLAG_COUNT" -ne 1 ] ||
       [ "${SCROOGE_INTERNAL_UPDATE:-}" != "1" ]; then
        printf "%b\n" "${RED}Error: --update is reserved for the internal update workflow.${NC}"
        exit 1
    fi
    if [ "$INSTALL_MODE" != "selected" ] || [ -z "$SELECTED" ]; then
        printf "%b\n" "${RED}Error: Internal update mode requires at least one explicit target.${NC}"
        exit 1
    fi
fi

# ==============================================================================
# PREREQUISITES
# ==============================================================================

cd "$SCRIPT_DIR"

require_python_310 python3 "./install.sh" || exit 1

if [ -d "$VENV_DIR" ]; then
    require_python_310 "$VENV_DIR/bin/python3" "./scripts/uninstall.sh then ./install.sh" || exit 1
fi

if ! python3 -c "import ensurepip" > /dev/null 2>&1; then
    printf "%b\n" "${RED}Error: The python venv module is not available. Please install it first.${NC}"
    exit 1
fi

require_systemctl

# Repair executable modes for every command entry point and the versioned hook.
for s in "$BASE_DIR"/install.sh "$BASE_DIR"/update.sh "$BASE_DIR"/scripts/*.sh \
    "$BASE_DIR"/scripts/dev/*.sh "$BASE_DIR"/.githooks/pre-push; do
    [ -e "$s" ] || continue
    chmod +x "$s"
done
for s in "$BASE_DIR"/scripts/lib/*.sh; do
    [ -e "$s" ] || continue
    chmod a-x "$s"
done


# ------------------------------------------------------------------------------
# PYTHON VIRTUAL ENVIRONMENT SETUP
# ------------------------------------------------------------------------------

# Initialize or update python virtual environment
VENV_NEWLY_CREATED=false
if [ ! -d "$VENV_DIR" ]; then
    printf "%b\n" "\n${CYAN}Creating python virtual environment...${NC}"
    if ! python3 -m venv "$VENV_DIR"; then
        printf "%b\n" "${RED}Error: Failed to create python virtual environment.${NC}\n"
        exit 1
    fi
    VENV_NEWLY_CREATED=true
else
    printf "%b\n" "\n${CYAN}Updating python packages in existing virtual environment...${NC}"
fi

require_python_310 "$VENV_DIR/bin/python3" "./scripts/uninstall.sh then ./install.sh" || exit 1

# Safely upgrade pip and install matching requirements
if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade pip; then
    printf "%b\n" "${RED}Error: Failed to upgrade pip in the virtual environment.${NC}\n"
    exit 1
fi

if [ -f "$REQUIREMENTS_FILE" ]; then
    if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade -r "$REQUIREMENTS_FILE"; then
        printf "%b\n" "${RED}Error: Failed to install packages from $REQUIREMENTS_FILE.${NC}\n"
        exit 1
    fi
else
    printf "%b\n" "${RED}Error: $REQUIREMENTS_FILE not found. The script cannot run without its dependencies.${NC}\n"
    exit 1
fi

if [ "$VENV_NEWLY_CREATED" = true ]; then
    printf "%b\n" "${GREEN}Python virtual environment successfully created.${NC}"
else
    printf "%b\n" "${GREEN}Python virtual environment successfully updated.${NC}"
fi

# ------------------------------------------------------------------------------
# PLUGIN DISCOVERY
# ------------------------------------------------------------------------------
# The venv now exists, so the catalog can be queried (the single source of truth
# for which scrapers exist). One systemd unit pair is generated per plugin.

load_plugin_manifest || true
ALL_PLUGINS="$(list_plugins || true)"
if [ -z "$ALL_PLUGINS" ]; then
    # Distinguishes a broken venv from a plugin whose discovery failed, and
    # surfaces the actual error instead of a generic "venv may be broken".
    catalog_diagnose || exit 1
fi

if [ "$INSTALL_MODE" = "selected" ]; then
    # PLUGINS is newline-joined (matching list_plugins' output shape): the
    # per-plugin dependency and missing-config loops below split it under
    # IFS=newline, where a space-joined list would arrive as one bogus
    # " name" item and silently skip every selected plugin's requirements.
    PLUGINS=""
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for sel in $SELECTED; do
        if stream_contains "$sel" "$ALL_PLUGINS"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        elif [ "$IS_UPDATE" -eq 1 ]; then
            # During an update the selection is derived from the installed units;
            # a plugin removed or renamed in the incoming version is no longer in
            # the catalog. Skip it (its orphaned unit was already stopped by
            # update.sh; uninstall clears it) rather than aborting the whole update.
            printf "%b\n" "${YELLOW}Note: Skipping '$sel' - no longer a registered scraper in this version.${NC}"
            printf "%b\n" "${YELLOW}      Its leftover units can be removed with: ${CYAN}./scripts/uninstall.sh --$sel${NC}"
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            printf "%b\n" "Available targets: ${CYAN}$(stream_for_display "$ALL_PLUGINS")${NC}"
            IFS="$OLD_IFS"
            exit 1
        fi
    done
    IFS="$OLD_IFS"
else
    PLUGINS="$ALL_PLUGINS"
fi

# ------------------------------------------------------------------------------
# PER-PLUGIN DEPENDENCIES
# ------------------------------------------------------------------------------
# The root requirements.txt installed above carries only the core framework. Each
# plugin may ship its own requirements.txt (next to its plugin.py) listing the
# transport/parsing libraries only it needs (e.g. tls-client, selenium). Only the
# requirements of the plugin(s) being provisioned are installed, so an install
# that skips a heavy scraper never pulls that scraper's dependencies.

if ! PLUGIN_REQS="$(list_plugin_requirements)"; then
    printf "%b\n" "${RED}Error: Failed to read per-plugin dependency metadata.${NC}\n"
    exit 1
fi
OLD_IFS="$IFS"
IFS='
'
PAIR_TAB="$(printf '\t')"
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for pair in $PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    req_path="${pair#*"$PAIR_TAB"}"
    stream_contains "$req_name" "$PLUGINS" || continue

    printf "%b\n" "${CYAN}Installing dependencies for the '$req_name' scraper...${NC}"
    if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade -r "$req_path"; then
        IFS="$OLD_IFS"
        printf "%b\n" "${RED}Error: Failed to install dependencies for the '$req_name' scraper.${NC}\n"
        exit 1
    fi
done
IFS="$OLD_IFS"

if ! "$VENV_DIR/bin/python3" -m pip check; then
    printf "%b\n" "${RED}Error: Installed core and plugin dependencies are incompatible.${NC}\n"
    exit 1
fi

# ------------------------------------------------------------------------------
# SYSTEMD SETUP
# ------------------------------------------------------------------------------

printf "%b\n" "\n${CYAN}Setting up Systemd timer(s)...${NC}"

mkdir -p "$SYSTEMD_USER_DIR"

# Resolve the one framework-owned OnCalendar value for each plugin. All other timer
# metadata is rendered by the shared framework writer.
if ! ALL_SCHEDULES="$(list_plugin_schedules)"; then
    printf "%b\n" "${RED}Error: Failed to resolve plugin schedules.${NC}\n"
    exit 1
fi

if [ -n "$PLUGINS" ]; then
    if [ "$IS_UPDATE" -eq 1 ]; then
        PROVISION_MODE="deferred"
    else
        PROVISION_MODE="normal"
    fi
    if ! provision_units_transaction "$PLUGINS" "$ALL_SCHEDULES" "$PROVISION_MODE"; then
        if [ "$IS_UPDATE" -eq 1 ]; then
            printf "%b\n" "${RED}Error: Transactional systemd provisioning failed during update.${NC}\n"
        else
            printf "%b\n" "${RED}Error: Transactional systemd provisioning failed.${NC}\n"
        fi
        [ -z "${PROVISION_RECOVERY_DIR:-}" ] || \
            printf "%b\n" "${YELLOW}Recovery files: $PROVISION_RECOVERY_DIR${NC}"
        exit 1
    fi
fi

if command -v loginctl >/dev/null 2>&1; then
    # $USER is conventionally exported but not guaranteed (clean env, some
    # containers/cron); fall back to `id -un` so `set -u` never aborts here.
    LINGER_USER="${USER:-$(id -un)}"
    if [ "$(loginctl show-user "$LINGER_USER" --property=Linger 2>/dev/null)" != "Linger=yes" ]; then
        printf "%b\n" "${CYAN}Enabling user lingering to allow timer to run when logged out...${NC}"
        # Non-fatal: lingering only lets timers run while logged out; without it the
        # install is still valid (timers run while logged in), so a failure here
        # (e.g. a system that requires root to enable linger) must not abort.
        loginctl enable-linger "$LINGER_USER" || printf "%b\n" "${YELLOW}Warning: Could not enable user lingering; timers will run only while you are logged in.${NC}"
    fi
fi

printf "%b\n" "${GREEN}Systemd timer(s) configured successfully.${NC}"

# ------------------------------------------------------------------------------
# LAST CHECKS
# ------------------------------------------------------------------------------
# Report any plugin whose products config file is still missing (non-fatal), and
# whether the shared general configuration is missing.

MISSING_CONFIGS=""
if ! EXAMPLE_PAIRS="$(list_plugin_examples)"; then
    printf "%b\n" "${RED}Error: Failed to read plugin configuration metadata.${NC}\n"
    exit 1
fi
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for plugin in $PLUGINS; do
    [ -f "config/$plugin.json" ] || MISSING_CONFIGS="$MISSING_CONFIGS $plugin"
done
IFS="$OLD_IFS"

GENERAL_CONFIG_MISSING=0
[ -f "config/general.json" ] || GENERAL_CONFIG_MISSING=1

if [ -n "$MISSING_CONFIGS" ] || [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
    printf "%b\n" "\n${YELLOW}Note: Configuration required!${NC}"

    for plugin in $MISSING_CONFIGS; do
        example="$(plugin_stream_value "$plugin" "$EXAMPLE_PAIRS")"
        printf "%b\n" "- Copy $example to config/$plugin.json"
        printf "%b\n" "  and fill it with your desired items."
    done

    if [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
        printf "%b\n" "- Copy src/core/general/config.example.json to config/general.json"
        printf "%b\n" "  and configure your Apprise notification URLs and preferences."
    fi

    printf "%b\n" "- Read the README.md file for more information."
fi

if [ "$IS_UPDATE" -eq 0 ]; then
    printf "%b\n" "\n${GREEN}Installation complete!${NC}\n"
fi
