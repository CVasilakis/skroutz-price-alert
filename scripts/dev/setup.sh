#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"
VENV_PYTHON="$PROJECT_ROOT/venv/bin/python3"
SELECTED=""
# Every catalog read in this file runs on the system interpreter, because all of
# them (help text, target validation, the dependency plan) happen before the venv
# this script is here to create necessarily exists. install.sh flips the same knob
# back to the venv interpreter once it has one; this script never needs to.
CATALOG_PYTHON=python3
SETUP_OUTPUT_STARTED=0
SETUP_SECTION_STARTED=0

print_help() {
    load_plugin_catalog || true
    _ph_targets="$(list_plugins 2>/dev/null || true)"
    printf '\n%s\n\n' "Usage: ./scripts/dev/setup.sh [-h] [--debug] [--<target>]"
    printf '%s\n' "Create or update the development venv without systemd or user-data"
    printf '%s\n\n' "changes. With no target, install every target's private dependencies."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    if [ -n "$_ph_targets" ]; then
        _ph_old_ifs="$IFS"
        IFS='
'
        for _ph_target in $_ph_targets; do
            _ph_display_name="$(plugin_display_name "$_ph_target")"
            printf '  --%-15s Install private dependencies for only the %s target\n' \
                "$_ph_target" "${_ph_display_name:-$_ph_target}"
        done
        IFS="$_ph_old_ifs"
    else
        printf '%s\n' "  --<target>        install private dependencies for only that target"
    fi
    printf '\n'
}

setup_begin() {
    if [ "$SETUP_OUTPUT_STARTED" -eq 0 ]; then
        begin_operational_output
        SETUP_OUTPUT_STARTED=1
    fi
}

setup_section() {
    setup_begin
    if [ "$SETUP_SECTION_STARTED" -eq 1 ]; then
        printf '\n'
    fi
    section_heading success "$@"
    SETUP_SECTION_STARTED=1
}

setup_finish() {
    [ "$SETUP_OUTPUT_STARTED" -eq 0 ] || end_operational_output
}

setup_exit() {
    _se_status="$1"
    setup_finish
    exit "$_se_status"
}

HELP_REQUESTED=0
for argument in "$@"; do
    case "$argument" in
        -h|--help) HELP_REQUESTED=1 ;;
    esac
done
if [ "$HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi

INVALID_ARGUMENT=''
for argument in "$@"; do
    case "$argument" in
        --debug)
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
        --)
            [ -n "$INVALID_ARGUMENT" ] || INVALID_ARGUMENT="Invalid argument: $argument"
            ;;
        --?*)
            if [ -n "$SELECTED" ]; then
                [ -n "$INVALID_ARGUMENT" ] ||
                    INVALID_ARGUMENT="Select at most one target."
            else
                SELECTED="${argument#--}"
                if ! is_valid_target "$SELECTED"; then
                    [ -n "$INVALID_ARGUMENT" ] ||
                        INVALID_ARGUMENT="Invalid target '$SELECTED' (expected a nonblank snake_case name)."
                fi
            fi
            ;;
        *)
            [ -n "$INVALID_ARGUMENT" ] || INVALID_ARGUMENT="Invalid argument: $argument"
            ;;
    esac
done

if [ -n "$INVALID_ARGUMENT" ]; then
    setup_section "Setup arguments"
    task_status failure "$INVALID_ARGUMENT"
    task_status info "Run ./scripts/dev/setup.sh --help for usage."
    setup_exit 1
fi

setup_section "Environment checks"
if ! run_action reject_project_venv_symlink; then
    task_status failure "The development venv path is a symlink."
    task_status info \
        "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh."
    setup_exit 1
fi
task_status success "The development venv path is safe."

if ! run_action require_python_310 python3 "./scripts/dev/setup.sh"; then
    task_status failure "System Python 3.10 or newer is required."
    task_status info \
        "Install a supported Python, then run ./scripts/dev/setup.sh again."
    setup_exit 1
fi
task_status success "System Python 3.10 or newer is available."

if [ -d "$PROJECT_ROOT/venv" ]; then
    if ! run_action require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh"; then
        task_status failure "The existing development venv uses an unsupported Python."
        task_status info \
            "Remove the venv directory, then run ./scripts/dev/setup.sh again."
        setup_exit 1
    fi
    task_status success "The existing development venv uses a supported Python."
else
    task_status info "A new development venv is required."
fi

if run_captured catalog_cli requirements; then
    PLUGIN_REQUIREMENTS="$CAPTURED_COMMAND_OUTPUT"
else
    requirements_status=$?
    task_status failure "Target dependency discovery failed."
    task_status info \
        "Fix the target catalog error, then run ./scripts/dev/setup.sh again."
    setup_exit "$requirements_status"
fi

FOUND=0
# Requirements report columns, as both loops below address them. The producer
# (core.scrapers.tooling.cli requirements) owns the contract:
#
#   $1 target  $2 requirements_path
#
# $2 is empty for a target with no private dependencies, which this reads as
# "core dependencies only" rather than filtering the row out; the shell's own
# list_plugin_requirements accessor drops those instead, because install.sh only
# ever wants something to install. The separator is spelled through printf
# because POSIX sh has no $'\t' and a literal tab in a ${...%%pattern} is
# invisible on screen -- the same idiom install.sh uses to parse this shape.
REQUIREMENTS_TAB="$(printf '\t')"
OLD_IFS="$IFS"
IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%"$REQUIREMENTS_TAB"*}"
    if [ -n "$SELECTED" ] && [ "$target" = "$SELECTED" ]; then
        FOUND=1
    fi
done
IFS="$OLD_IFS"

if [ -n "$SELECTED" ] && [ "$FOUND" -eq 0 ]; then
    task_status failure "Unknown target '$SELECTED'."
    task_status info "Run ./scripts/dev/setup.sh --help to list available targets."
    setup_exit 1
fi
if [ -n "$SELECTED" ]; then
    task_status success "Dependency plan loaded for the $SELECTED target."
else
    task_status success "Dependency plan loaded for all targets."
fi

setup_section "Python environment"
if [ -d "$PROJECT_ROOT/venv" ]; then
    task_status info "Updating the existing development venv."
else
    if run_with_progress "Creating the development Python environment..." \
        run_action python3 -m venv "$PROJECT_ROOT/venv"; then
        task_status success "Development venv created."
    else
        venv_status=$?
        task_status failure "Development venv creation failed."
        task_status info \
            "Fix the Python venv support, then run ./scripts/dev/setup.sh again."
        setup_exit "$venv_status"
    fi
fi
if ! run_action require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh"; then
    task_status failure "The development venv is not usable with Python 3.10 or newer."
    task_status info "Remove the venv directory, then run ./scripts/dev/setup.sh again."
    setup_exit 1
fi
task_status success "The development venv is ready."

setup_section "Development dependencies"
if run_with_progress "Updating Python packaging tools..." \
    run_action "$VENV_PYTHON" -m pip install --upgrade pip; then
    task_status success "Packaging tools updated."
else
    pip_status=$?
    task_status failure "Packaging tools could not be updated."
    task_status info \
        "Check package-index access, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$pip_status"
fi
if run_with_progress "Installing core and development dependencies..." \
    run_action "$VENV_PYTHON" -m pip install --upgrade -r "$PROJECT_ROOT/requirements.txt" \
    -r "$PROJECT_ROOT/scripts/dev/requirements-dev.txt"; then
    task_status success "Core and development dependencies installed."
else
    requirements_status=$?
    task_status failure "Core and development dependencies could not be installed."
    task_status info \
        "Check the requirements and package-index access, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$requirements_status"
fi

PRIVATE_REQUIREMENTS=0
IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%"$REQUIREMENTS_TAB"*}"
    requirement="${row#*"$REQUIREMENTS_TAB"}"
    [ -z "$SELECTED" ] || [ "$target" = "$SELECTED" ] || continue
    if [ -n "$requirement" ]; then
        PRIVATE_REQUIREMENTS=$((PRIVATE_REQUIREMENTS + 1))
        if run_with_progress "Installing private target dependencies..." \
            run_action "$VENV_PYTHON" -m pip install --upgrade -r "$requirement"; then
            :
        else
            plugin_status=$?
            IFS="$OLD_IFS"
            task_status failure \
                "Private dependencies for the $target target could not be installed."
            task_status info \
                "Check that target's requirements, then run ./scripts/dev/setup.sh --debug --$target."
            setup_exit "$plugin_status"
        fi
    fi
done
IFS="$OLD_IFS"
if [ "$PRIVATE_REQUIREMENTS" -eq 0 ]; then
    task_status info "No private target dependencies are required."
elif [ "$PRIVATE_REQUIREMENTS" -eq 1 ]; then
    task_status success "Private dependencies installed for 1 target."
else
    task_status success \
        "Private dependencies installed for $PRIVATE_REQUIREMENTS targets."
fi

if run_with_progress "Checking installed dependencies..." \
    run_action "$VENV_PYTHON" -m pip check; then
    task_status success "Installed dependencies are consistent."
else
    check_status=$?
    task_status failure "Installed dependencies are inconsistent."
    task_status info \
        "Resolve the reported dependency conflict, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$check_status"
fi

setup_section "Repository checks"
if (
    export SCROOGE_INSTALL_HOOKS_CONTEXT=setup
    run_with_progress "Enabling repository-local pre-push checks..." \
        run_action "$PROJECT_ROOT/scripts/dev/install-hooks.sh"
); then
    task_status success "Repository-local pre-push checks are enabled."
else
    hook_status=$?
    task_status failure "Repository-local pre-push checks could not be enabled."
    task_status info \
        "Run ./scripts/dev/install-hooks.sh --debug for recovery details."
    setup_exit "$hook_status"
fi

setup_section "Setup complete"
task_status success "Development environment is ready."
setup_exit 0
