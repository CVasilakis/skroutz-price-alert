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
        for _ph_target in $_ph_targets; do
            _ph_display_name="$(plugin_display_name "$_ph_target")"
            printf '  --%-15s Install private dependencies for only the %s target\n' \
                "$_ph_target" "${_ph_display_name:-$_ph_target}"
        done
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

setup_task() {
    _st_kind="$1"
    shift
    case "$_st_kind" in
        success) _st_marker='v'; _st_color="$GREEN" ;;
        failure) _st_marker='x'; _st_color="$RED" ;;
        info) _st_marker='i'; _st_color="$CYAN" ;;
        warning) _st_marker='!'; _st_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _st_prefix="    ${_st_color}[${_st_marker}]${NC} "
    _print_indented_wrapped "$_st_prefix" '        ' "$@"
}

setup_finish() {
    [ "$SETUP_OUTPUT_STARTED" -eq 0 ] || end_operational_output
}

setup_exit() {
    _se_status="$1"
    setup_finish
    exit "$_se_status"
}

# shellcheck disable=SC2329  # invoked indirectly through run_captured
requirements_report() {
    PYTHONPATH="$PROJECT_ROOT/src" \
        python3 -m core.scrapers.tooling.cli requirements
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
    setup_task failure "$INVALID_ARGUMENT"
    setup_task info "Run ./scripts/dev/setup.sh --help for usage."
    setup_exit 1
fi

setup_section "Environment checks"
if ! run_action reject_project_venv_symlink; then
    setup_task failure "The development venv path is a symlink."
    setup_task info \
        "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh."
    setup_exit 1
fi
setup_task success "The development venv path is safe."

if ! run_action require_python_310 python3 "./scripts/dev/setup.sh"; then
    setup_task failure "System Python 3.10 or newer is required."
    setup_task info \
        "Install a supported Python, then run ./scripts/dev/setup.sh again."
    setup_exit 1
fi
setup_task success "System Python 3.10 or newer is available."

if [ -d "$PROJECT_ROOT/venv" ]; then
    if ! run_action require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh"; then
        setup_task failure "The existing development venv uses an unsupported Python."
        setup_task info \
            "Remove the venv directory, then run ./scripts/dev/setup.sh again."
        setup_exit 1
    fi
    setup_task success "The existing development venv uses a supported Python."
else
    setup_task info "A new development venv is required."
fi

if run_captured requirements_report; then
    PLUGIN_REQUIREMENTS="$CAPTURED_COMMAND_OUTPUT"
else
    requirements_status=$?
    setup_task failure "Target dependency discovery failed."
    setup_task info \
        "Fix the target catalog error, then run ./scripts/dev/setup.sh again."
    setup_exit "$requirements_status"
fi

FOUND=0
OLD_IFS="$IFS"
IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%	*}"
    if [ -n "$SELECTED" ] && [ "$target" = "$SELECTED" ]; then
        FOUND=1
    fi
done
IFS="$OLD_IFS"

if [ -n "$SELECTED" ] && [ "$FOUND" -eq 0 ]; then
    setup_task failure "Unknown target '$SELECTED'."
    setup_task info "Run ./scripts/dev/setup.sh --help to list available targets."
    setup_exit 1
fi
if [ -n "$SELECTED" ]; then
    setup_task success "Dependency plan loaded for the $SELECTED target."
else
    setup_task success "Dependency plan loaded for all targets."
fi

setup_section "Python environment"
if [ -d "$PROJECT_ROOT/venv" ]; then
    setup_task info "Updating the existing development venv."
else
    if run_action python3 -m venv "$PROJECT_ROOT/venv"; then
        setup_task success "Development venv created."
    else
        venv_status=$?
        setup_task failure "Development venv creation failed."
        setup_task info \
            "Fix the Python venv support, then run ./scripts/dev/setup.sh again."
        setup_exit "$venv_status"
    fi
fi
if ! run_action require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh"; then
    setup_task failure "The development venv is not usable with Python 3.10 or newer."
    setup_task info "Remove the venv directory, then run ./scripts/dev/setup.sh again."
    setup_exit 1
fi
setup_task success "The development venv is ready."

setup_section "Development dependencies"
if run_action "$VENV_PYTHON" -m pip install --upgrade pip; then
    setup_task success "Packaging tools updated."
else
    pip_status=$?
    setup_task failure "Packaging tools could not be updated."
    setup_task info \
        "Check package-index access, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$pip_status"
fi
if run_action "$VENV_PYTHON" -m pip install --upgrade -r "$PROJECT_ROOT/requirements.txt" \
    -r "$PROJECT_ROOT/scripts/dev/requirements-dev.txt"; then
    setup_task success "Core and development dependencies installed."
else
    requirements_status=$?
    setup_task failure "Core and development dependencies could not be installed."
    setup_task info \
        "Check the requirements and package-index access, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$requirements_status"
fi

PRIVATE_REQUIREMENTS=0
IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%	*}"
    requirement="${row#*	}"
    [ -z "$SELECTED" ] || [ "$target" = "$SELECTED" ] || continue
    if [ -n "$requirement" ]; then
        PRIVATE_REQUIREMENTS=$((PRIVATE_REQUIREMENTS + 1))
        if run_action "$VENV_PYTHON" -m pip install --upgrade -r "$requirement"; then
            :
        else
            plugin_status=$?
            IFS="$OLD_IFS"
            setup_task failure \
                "Private dependencies for the $target target could not be installed."
            setup_task info \
                "Check that target's requirements, then run ./scripts/dev/setup.sh --debug --$target."
            setup_exit "$plugin_status"
        fi
    fi
done
IFS="$OLD_IFS"
if [ "$PRIVATE_REQUIREMENTS" -eq 0 ]; then
    setup_task info "No private target dependencies are required."
elif [ "$PRIVATE_REQUIREMENTS" -eq 1 ]; then
    setup_task success "Private dependencies installed for 1 target."
else
    setup_task success \
        "Private dependencies installed for $PRIVATE_REQUIREMENTS targets."
fi

if run_action "$VENV_PYTHON" -m pip check; then
    setup_task success "Installed dependencies are consistent."
else
    check_status=$?
    setup_task failure "Installed dependencies are inconsistent."
    setup_task info \
        "Resolve the reported dependency conflict, then run ./scripts/dev/setup.sh --debug."
    setup_exit "$check_status"
fi

setup_section "Repository checks"
if (
    export SCROOGE_INSTALL_HOOKS_CONTEXT=setup
    run_action "$PROJECT_ROOT/scripts/dev/install-hooks.sh"
); then
    setup_task success "Repository-local pre-push checks are enabled."
else
    hook_status=$?
    setup_task failure "Repository-local pre-push checks could not be enabled."
    setup_task info \
        "Run ./scripts/dev/install-hooks.sh --debug for recovery details."
    setup_exit "$hook_status"
fi

setup_section "Setup complete"
setup_task success "Development environment is ready."
setup_exit 0
