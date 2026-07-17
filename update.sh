#!/bin/sh
set -eu

# Wrap the update process in a function to avoid script changes
# due to git pull on the run-time
main() {
    # ==============================================================================
    # GLOBAL VARIABLES
    # ==============================================================================

    # Get the directory where the script is located (repository root)
    SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
    BASE_DIR="$SCRIPT_DIR"

    # Shared helpers. Sourced here, inside main(), so the library is fully read
    # into memory BEFORE 'git reset' may rewrite it on disk - the same reason the
    # whole update runs from within this function.
    # shellcheck source=scripts/lib/common.sh
    . "$SCRIPT_DIR/scripts/lib/common.sh"

    # ==============================================================================
    # ARGUMENTS
    # ==============================================================================
    # update.sh takes no plugin flags: it always reprovisions exactly the set of
    # scrapers that were previously installed (derived later from the units on
    # disk). Only -h/--help is accepted; anything else is a typo.

    print_help() {
        printf '\n'
        printf '%s\n' "Update Scrooge Alert to the latest version."
        printf '%s\n' "Reinstalls the scraper(s) you previously installed."
        printf '\n'
    }

    if [ "$#" -gt 0 ]; then
        case "$1" in
            -h|--help) print_help; exit 0 ;;
            *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
        esac
    fi

    # The update ends in a reprovision (install.sh), which requires systemd - check
    # up front so a systemd-less host fails BEFORE the destructive git reset, not
    # halfway through with the working tree already overwritten.
    require_systemctl

    # ==============================================================================
    # EXECUTION
    # ==============================================================================

    printf "%b\n" "\n${CYAN}Updating Scrooge Alert...${NC}"

    cd "$SCRIPT_DIR"

    CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
    IS_DIRTY=$(git status --porcelain)

    if [ -n "$IS_DIRTY" ]; then
        printf "%b\n" "\n${YELLOW}Uncommitted modifications detected in your working directory.${NC}"
        printf "%b\n" "${YELLOW}This update will reset to the remote 'main' branch and discard any uncommitted changes${NC}."
        printf "%b\n" "${YELLOW}Please commit or stash your work before proceeding to avoid data loss.${NC}"
        printf "Do you want to proceed and discard these changes? [y/N]: "
        # `|| response=""` keeps `set -e` from aborting on EOF (e.g. a non-interactive
        # stdin); an empty answer then falls through to the abort case below.
        read -r response || response=""
        case "$response" in
            [Yy]* ) ;;
            * ) printf "%b\n" "\n${RED}Update aborted by the user.${NC}\n"; exit 1 ;;
        esac
    elif [ "$CURRENT_BRANCH" != "main" ]; then
        printf "%b\n" "\n${YELLOW}Switching from branch '${CURRENT_BRANCH}' to 'main'.${NC}"
    fi

    # Derive which plugins to reprovision from the units already installed, so an
    # update preserves the user's original selection (a single-plugin install stays
    # single) instead of silently provisioning every registered plugin - including
    # new or heavy ones the user never opted into. A store added in the new version
    # is adopted only by an explicit ./install.sh --<store>. Glob-based, so it needs
    # no venv and is robust to plugin renames in the incoming version.
    #
    # Captured before 'git reset' for clarity, though the unit files live under
    # ~/.config/systemd/user and are unaffected by it.
    INSTALLED_PLUGINS="$(list_installed_plugins timer)"

    # Ensure no scraper is actively running while we change the files out from
    # under it. Glob the installed services (also catches an orphaned service
    # whose timer is gone).
    STOP_FAILED=0
    for plugin in $(list_installed_plugins service); do
        if ! stop_one "$plugin"; then
            printf "%b\n" "${RED}Error: Could not safely stop the '$plugin' scraper before updating.${NC}"
            STOP_FAILED=1
        fi
    done
    if [ "$STOP_FAILED" -ne 0 ]; then
        printf "%b\n" "${RED}Update aborted before changing project files.${NC}\n"
        exit 1
    fi

    if ! git checkout -f main --quiet; then
        printf "%b\n" "\n${RED}Error: Failed to checkout 'main' branch. Update aborted.${NC}\n"
        exit 1
    fi

    if ! git fetch --all --quiet; then
        printf "%b\n" "\n${RED}Error: Failed to fetch latest changes from the repository. Update aborted.${NC}\n"
        exit 1
    fi

    if ! git reset --hard origin/main --quiet; then
        printf "%b\n" "\n${RED}Error: Failed to reset to origin/main. Update aborted.${NC}\n"
        exit 1
    fi

    if [ -f "$SCRIPT_DIR/install.sh" ]; then
        chmod +x "$SCRIPT_DIR/install.sh"

        # Turn the bare plugin names from the glob into install.sh's --<plugin>
        # flags. When nothing was installed this stays empty and the call below
        # collapses to a bare '--update', i.e. a full install (fresh bootstrap /
        # legacy behavior).
        UPDATE_FLAGS=""
        for plugin in $INSTALLED_PLUGINS; do
            UPDATE_FLAGS="$UPDATE_FLAGS --$plugin"
        done

        # Reprovision exactly the previously-installed plugins. Intentionally
        # unquoted so the flag list splits into separate arguments.
        # shellcheck disable=SC2086
        if ! "$SCRIPT_DIR/install.sh" --update $UPDATE_FLAGS; then
            printf "%b\n" "\n${RED}Error: Installation failed during update. Please run ./install.sh manually.${NC}\n"
            exit 1
        fi
    fi

    # Surface any scraper this version provides that the user has not installed
    # (registry minus installed timers), so a newly-added store is discoverable
    # instead of silently waiting to be opted into. The venv is fresh after the
    # reprovision above, so the registry is readable here.
    NEW_SCRAPERS=""
    INSTALLED_NOW="$(list_installed_plugins timer)"
    for plugin in $(list_plugins 2>/dev/null || true); do
        plugin_in_list "$plugin" $INSTALLED_NOW || NEW_SCRAPERS="$NEW_SCRAPERS $plugin"
    done
    if [ -n "$NEW_SCRAPERS" ]; then
        printf "%b\n" "\n${YELLOW}Scrapers available but not installed:${NC}${CYAN}$(printf ' %s' $NEW_SCRAPERS)${NC}"
        printf "%b\n" "Install any of them with: ${CYAN}./install.sh --<target>${NC}"
    fi

    printf "%b\n" "\n${GREEN}Update complete! You are now running the latest version.${NC}\n"

    # Exit immediately to prevent the shell from reading any new lines
    # if this script was modified by 'git pull'
    exit 0
}

main "$@"
