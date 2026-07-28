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

print_help() {
    load_plugin_catalog || true
    _ph_targets="$(list_plugins 2>/dev/null || true)"
    printf '\n%s\n\n' "Usage: ./scripts/dev/setup.sh [-h] [--debug] [--<target>]"
    printf '%s\n' "Create or update the development venv without systemd or user-data"
    printf '%s\n\n' "changes. With no target, install every plugin's private dependencies."
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

while [ "$#" -gt 0 ]; do
    case "$1" in
        --debug)
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
        --) printf '%s\n' "Error: Invalid argument: $1" >&2; exit 1 ;;
        --?*)
            [ -z "$SELECTED" ] || {
                printf '%s\n' "Error: Select at most one target." >&2
                exit 1
            }
            SELECTED="${1#--}"
            require_valid_target "$SELECTED" || exit 1
            ;;
        *) printf '%s\n' "Error: Invalid argument: $1" >&2; exit 1 ;;
    esac
    shift
done

reject_project_venv_symlink || exit 1
require_python_310 python3 "./scripts/dev/setup.sh" || exit 1
if [ -d "$PROJECT_ROOT/venv" ]; then
    require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh" || exit 1
fi
PLUGIN_REQUIREMENTS="$(
    PYTHONPATH="$PROJECT_ROOT/src" python3 -m core.scrapers.tooling.cli requirements
)"
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
    printf '%s\n' "Error: Unknown target '$SELECTED'." >&2
    exit 1
fi

[ -d "$PROJECT_ROOT/venv" ] || python3 -m venv "$PROJECT_ROOT/venv"
require_python_310 "$VENV_PYTHON" "./scripts/dev/setup.sh" || exit 1
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install --upgrade -r "$PROJECT_ROOT/requirements.txt" \
    -r "$PROJECT_ROOT/scripts/dev/requirements-dev.txt"

IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%	*}"
    requirement="${row#*	}"
    [ -z "$SELECTED" ] || [ "$target" = "$SELECTED" ] || continue
    if [ -n "$requirement" ]; then
        "$VENV_PYTHON" -m pip install --upgrade -r "$requirement"
    fi
done
IFS="$OLD_IFS"
"$VENV_PYTHON" -m pip check
if (
    export SCROOGE_INSTALL_HOOKS_CONTEXT=setup
    run_action "$PROJECT_ROOT/scripts/dev/install-hooks.sh"
); then
    printf '    %b[v]%b %s\n' "$GREEN" "$NC" \
        "Repository-local pre-push checks are enabled."
else
    hook_status=$?
    printf '    %b[x]%b %s\n' "$RED" "$NC" \
        "Repository-local pre-push checks could not be enabled." >&2
    printf '%s\n' \
        "        Run ./scripts/dev/install-hooks.sh --debug for recovery details." >&2
    exit "$hook_status"
fi
printf '%s\n' "Development environment is ready."
