#!/bin/sh
# Provision one timer+service pair per selected target.
#
# Selection policy: registered, the catalog alone, because only a registered
# plugin supplies the sources, dependencies, and interval a unit is rendered
# from. The deferred update context is the one exception and does not use the
# shared policy. update.sh re-invokes this script with
# SCROOGE_INSTALL_CONTEXT=deferred and an explicit --<target> flag per target it
# preserved, and that mode re-filters those flags against the catalog itself, so
# a target that stopped being registered upstream is reported and skipped rather
# than failing the run. It is a context, not a flag: it additionally refuses
# unless SCROOGE_INTERNAL_UPDATE=1 and the flag set is explicit and non-empty,
# so no one can reach it by passing an argument.

set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# Shared helpers (colors, plugin enumeration, systemd helpers)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/lib/preflight.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"
# shellcheck source=scripts/lib/provisioning.sh
. "$SCRIPT_DIR/lib/provisioning.sh"

# Environment and File Configurations
VENV_DIR="venv"
REQUIREMENTS_FILE="requirements.txt"
INSTALL_OUTPUT_STARTED=0
INSTALL_SECTION_STARTED=0
IS_UPDATE=0

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    load_plugin_catalog || true
    printf '\n'
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = install ]; then
        printf '%s\n' "Usage: ./scrooge-alert install [--help] [--debug] [--<target> ...]"
    else
        printf '%s\n' "Usage: install.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '\n'
    printf '%s\n' "Set up the Python virtual environment and install the systemd timer(s) and"
    printf '%s\n' "service(s). With no target flag every registered scraper is installed and"
    printf '%s\n' "enabled; pass one or more --<target> flags to install only those. You can"
    printf '%s\n' "run this command as many times as you like - run it again in the future"
    printf '%s\n' "to install additional scrapers."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    for plugin in $(list_plugins 2>/dev/null || true); do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Install and enable only the %s scraper\n' "$plugin" "${display_name:-$plugin}"
    done
    printf '\n'
}

install_begin() {
    if [ "$INSTALL_OUTPUT_STARTED" -eq 0 ]; then
        begin_operational_output
        INSTALL_OUTPUT_STARTED=1
    fi
}

install_section() {
    install_begin
    if [ "$INSTALL_SECTION_STARTED" -eq 1 ]; then
        printf '\n'
    fi
    section_heading success "$@"
    INSTALL_SECTION_STARTED=1
}

install_warning_section() {
    install_begin
    if [ "$INSTALL_SECTION_STARTED" -eq 1 ]; then
        printf '\n'
    fi
    section_heading warning "$@"
    INSTALL_SECTION_STARTED=1
}

install_command() {
    printf '        %s\n' "$1"
}

install_finish() {
    if [ "$IS_UPDATE" -eq 0 ] && [ "$INSTALL_OUTPUT_STARTED" -eq 1 ]; then
        end_operational_output
    fi
}

install_exit() {
    _ie_status="$1"
    install_finish
    exit "$_ie_status"
}

install_fail() {
    task_status failure "$1"
    shift
    for _if_guidance in "$@"; do
        task_status info "$_if_guidance"
    done
    install_exit 1
}

pip_install() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        run_action "$VENV_DIR/bin/python3" -m pip install --upgrade "$@"
    else
        run_action "$VENV_DIR/bin/python3" -m pip install -q --upgrade "$@"
    fi
}

install_load_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if run_captured catalog_cli catalog; then
        PLUGIN_CATALOG_DATA="$CAPTURED_COMMAND_OUTPUT"
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_DATA=''
    PLUGIN_CATALOG_STATE=2
    return 1
}

install_load_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if run_captured catalog_cli schedules --config-dir "$BASE_DIR/config"; then
        PLUGIN_SCHEDULE_DATA="$CAPTURED_COMMAND_OUTPUT"
        PLUGIN_SCHEDULE_STATE=1
        return 0
    fi
    PLUGIN_SCHEDULE_DATA=''
    PLUGIN_SCHEDULE_STATE=2
    return 1
}

cd "$BASE_DIR"
CATALOG_PYTHON=python3
INHERITED_DEBUG="$DEBUG_MODE"
for argument in "$@"; do
    case "$argument" in
        --debug)
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
    esac
done
if ! run_action parse_target_flags "$@"; then
    install_section "Installation arguments"
    task_status failure "The command-line arguments are invalid."
    task_status info "Run $(command_text './scrooge-alert install --help') for usage."
    install_exit 1
fi
if [ "$TARGET_HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi
case "${SCROOGE_INSTALL_CONTEXT:-normal}" in
    normal) IS_UPDATE=0 ;;
    deferred)
        IS_UPDATE=1
        if [ "${SCROOGE_INTERNAL_UPDATE:-}" != 1 ] ||
            [ "$TARGET_FLAGS_EXPLICIT" -ne 1 ] ||
            [ -z "$TARGET_FLAGS" ]; then
            install_section "Installation context"
            install_fail "The internal deferred-install context is invalid." \
                "Rerun $(command_text './scrooge-alert update') to restart the update safely."
        fi
        if [ "$INHERITED_DEBUG" -eq 1 ]; then
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
        fi
        ;;
    *)
        install_section "Installation context"
        install_fail "The installation context is invalid." \
            "Run $(command_text './scrooge-alert install') directly without internal environment overrides."
        ;;
esac

install_section "Installation checks"
if ! run_action reject_project_venv_symlink; then
    install_fail "The project venv path is a symlink." \
        "Remove the venv symlink, then run $(command_text './scrooge-alert install') again."
fi
task_status success "The project venv path is safe."
if ! run_action require_python_310 python3 "$(command_text './scrooge-alert install')"; then
    install_fail "System Python 3.10 or newer is required." \
        "Install a supported Python, then run $(command_text './scrooge-alert install') again."
fi
task_status success "System Python 3.10 or newer is available."

# Validate the import-light catalog, selection, source inputs, and every unit
# destination before venv creation or package installation.
if ! install_load_catalog; then
    run_action catalog_diagnose || true
    install_fail "The target catalog could not be loaded." \
        "Fix the catalog error, then run $(command_text './scrooge-alert install --debug')."
fi
if run_captured list_plugins; then
    ALL_PLUGINS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "The registered targets could not be read." \
        "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
fi
if [ "$IS_UPDATE" -eq 0 ]; then
    if ! run_action select_targets registered; then
        install_fail "The requested target selection is invalid." \
            "Run $(command_text './scrooge-alert install --help') to list available targets."
    fi
    PLUGINS="$SELECTED_TARGETS"
else
    PLUGINS=''
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for sel in $TARGET_FLAGS; do
        if stream_contains "$sel" "$ALL_PLUGINS"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        else
            task_status info \
                "Target '$sel' is no longer registered; its units remain disabled."
            task_status info "Remove them with $(command_text "./scrooge-alert uninstall --$sel")."
        fi
    done
    IFS="$OLD_IFS"
fi

for required_file in scrooge-alert requirements.txt scripts/run.sh scripts/lib/common.sh \
    scripts/lib/runtime.sh scripts/lib/preflight.sh scripts/lib/systemd.sh \
    scripts/lib/provisioning.sh; do
    if ! run_action require_regular_owned_file "$BASE_DIR/$required_file"; then
        install_fail "Required project file '$required_file' is missing or unsafe." \
            "Restore the regular project file, then run $(command_text './scrooge-alert install') again."
    fi
done
if run_captured list_plugin_requirements; then
    EARLY_PLUGIN_REQS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Target dependency metadata could not be read." \
        "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
fi
OLD_IFS="$IFS"
IFS='
'
PAIR_TAB="$(printf '\t')"
# shellcheck disable=SC2086
for pair in $EARLY_PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    req_path="${pair#*"$PAIR_TAB"}"
    stream_contains "$req_name" "$PLUGINS" || continue
    if ! run_action require_regular_owned_file "$req_path"; then
        IFS="$OLD_IFS"
        install_fail "[$req_name] Its requirements file is missing or unsafe." \
            "Restore the target's regular requirements.txt, then run $(command_text './scrooge-alert install') again."
    fi
done
IFS="$OLD_IFS"
if ! run_action require_renderable_base_dir; then
    install_fail "systemd cannot read this project path safely." \
        "Move the checkout to a path with no % \\ \" ' or newline characters, then run $(command_text './scrooge-alert install') again."
fi
if ! run_action validate_unit_destinations "$PLUGINS" pair; then
    install_fail "A managed systemd unit destination is unsafe." \
        "Remove the unsafe unit with $(command_text './scrooge-alert uninstall --<target>'), then retry."
fi

if [ -d "$VENV_DIR" ]; then
    if ! run_action require_python_310 \
        "$VENV_DIR/bin/python3" "$(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')"; then
        install_fail "The existing Python environment is unusable." \
            "Run $(command_text './scrooge-alert uninstall'), then run $(command_text './scrooge-alert install') again."
    fi
    task_status success "The existing Python environment uses a supported Python."
else
    task_status info "A new Python environment is required."
fi

if ! run_action python3 -c "import ensurepip"; then
    install_fail "Python venv support is not available." \
        "Install the Python venv module, then run $(command_text './scrooge-alert install') again."
fi
task_status success "Python venv support is available."

if ! run_action require_systemctl; then
    install_fail "Systemd user services are not available." \
        "Install or enable systemd user services, then run $(command_text './scrooge-alert install') again."
fi
task_status success "Systemd user services are available."

# ------------------------------------------------------------------------------
# PYTHON VIRTUAL ENVIRONMENT SETUP
# ------------------------------------------------------------------------------

# Initialize or update python virtual environment
VENV_NEWLY_CREATED=false
install_section "Python environment"
if [ ! -d "$VENV_DIR" ]; then
    if ! run_with_progress "Creating the project Python environment..." \
        run_action python3 -m venv "$VENV_DIR"; then
        install_fail "The Python environment could not be created." \
            "Fix Python venv support, then run $(command_text './scrooge-alert install --debug')."
    fi
    task_status success "Created a new Python environment."
    VENV_NEWLY_CREATED=true
else
    task_status info "Updating the existing Python environment."
fi

if ! run_action require_python_310 \
    "$VENV_DIR/bin/python3" "$(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')"; then
    install_fail "The Python environment is not usable with Python 3.10 or newer." \
        "Run $(command_text './scrooge-alert uninstall'), then run $(command_text './scrooge-alert install') again."
fi
task_status success "The Python environment is ready."

# Safely upgrade pip and install matching requirements
if ! run_with_progress "Updating Python packaging tools..." pip_install pip; then
    install_fail "Packaging tools could not be updated." \
        "Check package-index access, then run $(command_text './scrooge-alert install --debug')."
fi
task_status success "Packaging tools updated."

if [ -f "$REQUIREMENTS_FILE" ]; then
    if ! run_with_progress "Installing core dependencies..." \
        pip_install -r "$REQUIREMENTS_FILE"; then
        install_fail "Core dependencies could not be installed." \
            "Check requirements.txt and package-index access, then run $(command_text './scrooge-alert install --debug')."
    fi
else
    install_fail "$REQUIREMENTS_FILE was not found." \
        "Restore requirements.txt, then run $(command_text './scrooge-alert install') again."
fi

if [ "$VENV_NEWLY_CREATED" = true ]; then
    task_status success "Installed core dependencies."
else
    task_status success "Updated core dependencies."
fi

# Re-read the same import-light metadata through the completed venv before
# installing plugin-private dependencies and resolving schedules.
CATALOG_PYTHON="$BASE_DIR/venv/bin/python3"
reset_catalog_cache
if ! install_load_catalog; then
    run_action catalog_diagnose || true
    install_fail "The target catalog could not be loaded from the completed environment." \
        "Fix the catalog error, then run $(command_text './scrooge-alert install --debug')."
fi
if run_captured list_plugins; then
    FINAL_PLUGINS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "The registered targets could not be re-read." \
        "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
fi
if [ "$FINAL_PLUGINS" != "$ALL_PLUGINS" ]; then
    install_fail "The target catalog changed during installation." \
        "Retry $(command_text './scrooge-alert install') after the source tree is stable."
fi

# ------------------------------------------------------------------------------
# PER-PLUGIN DEPENDENCIES
# ------------------------------------------------------------------------------
# The root requirements.txt installed above carries only the core framework. Each
# plugin may ship its own requirements.txt (next to its plugin.py) listing the
# transport/parsing libraries only it needs (e.g. tls-client, selenium). Only the
# requirements of the plugin(s) being provisioned are installed, so an install
# that skips a heavy scraper never pulls that scraper's dependencies.

if run_captured list_plugin_requirements; then
    PLUGIN_REQS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Target dependency metadata could not be read." \
        "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
fi

HAS_PLUGIN_REQS=0
OLD_IFS="$IFS"
IFS='
'
PAIR_TAB="$(printf '\t')"
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for pair in $PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    if stream_contains "$req_name" "$PLUGINS"; then
        HAS_PLUGIN_REQS=1
        break
    fi
done

if [ "$HAS_PLUGIN_REQS" -eq 1 ]; then
    install_section "Target dependencies"
fi

for pair in $PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    req_path="${pair#*"$PAIR_TAB"}"
    stream_contains "$req_name" "$PLUGINS" || continue

    if ! run_with_progress "[$req_name] Installing private dependencies..." \
        pip_install -r "$req_path"; then
        IFS="$OLD_IFS"
        install_fail "[$req_name] Its private dependencies could not be installed." \
            "Check that target's requirements, then run $(command_text "./scrooge-alert install --debug --$req_name")."
    fi
    task_status success "[$req_name] Installed private dependencies."
done
IFS="$OLD_IFS"

if ! run_with_progress "Checking installed dependencies..." \
    run_action "$VENV_DIR/bin/python3" -m pip check; then
    install_fail "Installed core and target dependencies are incompatible." \
        "Resolve the dependency conflict, then run $(command_text './scrooge-alert install --debug')."
fi
if [ "$HAS_PLUGIN_REQS" -eq 1 ]; then
    task_status success "All installed dependencies are compatible."
else
    task_status success "Core dependencies are compatible."
fi

# ------------------------------------------------------------------------------
# SYSTEMD SETUP
# ------------------------------------------------------------------------------

install_section "Target provisioning"

if ! run_action mkdir -p "$SYSTEMD_USER_DIR"; then
    install_fail "The systemd user directory could not be created." \
        "Fix its ownership or permissions, then run $(command_text './scrooge-alert install --debug')."
fi

# Resolve config-dependent schedules separately from the immutable plugin catalog.
# A structurally invalid config excludes only its own target from this transaction.
if ! install_load_schedules; then
    install_fail "Target scheduling metadata could not be resolved." \
        "Fix the target configuration, then run $(command_text './scrooge-alert install --debug')."
fi
if run_captured list_plugin_schedules; then
    ALL_SCHEDULES="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Resolved target schedules could not be read." \
        "Fix the target configuration, then run $(command_text './scrooge-alert install --debug')."
fi
if run_captured list_interval_status; then
    INTERVAL_STATUS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Target schedule statuses could not be read." \
        "Fix the target configuration, then run $(command_text './scrooge-alert install --debug')."
fi
if run_captured list_schedule_errors; then
    SCHEDULE_ERRORS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Target schedule errors could not be read." \
        "Fix the target configuration, then run $(command_text './scrooge-alert install --debug')."
fi

CONFIG_FAILED=0
PROVISION_PLUGINS=""
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for plugin in $PLUGINS; do
    status="$(plugin_stream_value "$plugin" "$INTERVAL_STATUS" || true)"
    if [ -z "$status" ]; then
        IFS="$OLD_IFS"
        install_fail "No scheduling result was returned for target '$plugin'." \
            "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
    fi
    if [ "$status" = "error" ]; then
        schedule_error="$(plugin_stream_value "$plugin" "$SCHEDULE_ERRORS" || true)"
        task_status failure \
            "[$plugin] ${schedule_error:-Could not resolve its timer schedule.}"
        task_status info "[$plugin] Existing systemd units were left unchanged."
        CONFIG_FAILED=1
        continue
    fi
    PROVISION_PLUGINS="$(stream_add_unique "$PROVISION_PLUGINS" "$plugin")"
done
IFS="$OLD_IFS"

if [ -n "$PROVISION_PLUGINS" ]; then
    # A direct install activates each provisioned timer itself. Under an update
    # the timers stay quiesced: update.sh disabled them before the fast-forward
    # and holds the pre-quiescence states, so it owns the reactivation. See the
    # activation matrix above replace_units_transaction in lib/provisioning.sh.
    if [ "$IS_UPDATE" -eq 1 ]; then
        PROVISION_MODE="deferred"
    else
        PROVISION_MODE="normal"
    fi
    if ! run_with_progress "Configuring selected target timers..." \
        run_action provision_units_transaction \
        "$PROVISION_PLUGINS" "$ALL_SCHEDULES" "$PROVISION_MODE"; then
        if [ "$IS_UPDATE" -eq 1 ]; then
            task_status failure \
                "Transactional systemd provisioning failed during the update."
        else
            task_status failure "Transactional systemd provisioning failed."
        fi
        if [ -n "${UNIT_RECOVERY_DIR:-}" ]; then
            task_status warning "Recovery files were retained at $UNIT_RECOVERY_DIR."
        fi
        task_status info \
            "Rerun $(command_text './scrooge-alert install --debug'), or inspect with $(command_text './scrooge-alert status')."
        install_exit 1
    fi
    task_status success "Configured timers for the selected targets."
else
    if [ -n "$PLUGINS" ]; then
        task_status info "No valid selected targets could be provisioned."
    else
        task_status info "No registered targets require systemd provisioning."
    fi
fi

if command -v loginctl >/dev/null 2>&1; then
    # $USER is conventionally exported but not guaranteed (clean env, some
    # containers/cron); fall back to `id -un` so `set -u` never aborts here.
    if [ -n "${USER:-}" ]; then
        LINGER_USER="$USER"
    elif run_captured id -un; then
        LINGER_USER="$CAPTURED_COMMAND_OUTPUT"
    else
        task_status warning \
            "Could not identify the user for lingering; timers may run only while logged in."
        LINGER_USER=''
    fi
    LINGER_STATUS=''
    if [ -n "$LINGER_USER" ] &&
       run_captured loginctl show-user "$LINGER_USER" --property=Linger; then
        LINGER_STATUS="$CAPTURED_COMMAND_OUTPUT"
    fi
    if [ -n "$LINGER_USER" ] && [ "$LINGER_STATUS" != "Linger=yes" ]; then
        # Non-fatal: lingering only lets timers run while logged out; without it the
        # install is still valid (timers run while logged in), so a failure here
        # (e.g. a system that requires root to enable linger) must not abort.
        if run_action loginctl enable-linger "$LINGER_USER"; then
            task_status success "Enabled user lingering."
        else
            task_status warning \
                "Could not enable user lingering; timers will run only while logged in."
            task_status info \
                "Ask the system administrator to enable lingering for '$LINGER_USER'."
        fi
    fi
fi

# ------------------------------------------------------------------------------
# LAST CHECKS
# ------------------------------------------------------------------------------
# Report any plugin whose products config file is still missing (non-fatal), and
# whether the shared general configuration is missing.

MISSING_CONFIGS=""
if run_captured list_plugin_examples; then
    EXAMPLE_PAIRS="$CAPTURED_COMMAND_OUTPUT"
else
    install_fail "Target configuration metadata could not be read." \
        "Fix the target catalog, then run $(command_text './scrooge-alert install --debug')."
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
    install_warning_section "Configuration required"
    CONFIG_COMMANDS=''

    for plugin in $MISSING_CONFIGS; do
        example="$(plugin_stream_value "$plugin" "$EXAMPLE_PAIRS")"
        case "$example" in
            "$BASE_DIR"/*) example="${example#"$BASE_DIR"/}" ;;
        esac
        task_status warning "[$plugin] Its tracked-items configuration is missing."
        CONFIG_COMMANDS="${CONFIG_COMMANDS}${CONFIG_COMMANDS:+
}cp $example config/$plugin.json"
    done

    if [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
        task_status warning "The general configuration is missing."
        CONFIG_COMMANDS="${CONFIG_COMMANDS}${CONFIG_COMMANDS:+
}cp src/core/general/config.example.json config/general.json"
    fi

    task_status info "From the project directory, run:"
    [ -d config ] || install_command "mkdir -p config"
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only command iteration
    for config_command in $CONFIG_COMMANDS; do
        install_command "$config_command"
    done
    IFS="$OLD_IFS"
    if [ -n "$MISSING_CONFIGS" ]; then
        task_status info \
            "Fill each new target configuration with the items you want to track."
    fi
    if [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
        task_status info \
            "Configure notification URLs and preferences in config/general.json."
    fi
    task_status info "Read README.md for configuration guidance."
fi

if [ "$IS_UPDATE" -eq 0 ]; then
    if [ "$CONFIG_FAILED" -eq 0 ]; then
        install_section "Installation result"
        task_status success "Installation complete."
    fi
fi

if [ "$CONFIG_FAILED" -ne 0 ]; then
    install_section "Installation result"
    task_status failure \
        "One or more targets were skipped because their configuration is invalid."
    task_status info \
        "Fix each reported target configuration, then run $(command_text './scrooge-alert install') again."
    install_exit "$EXIT_STATUS_TARGET_CONFIG_ERROR"
fi

install_finish
