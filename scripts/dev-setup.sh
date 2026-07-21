#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
VENV_PYTHON="$SCRIPT_DIR/venv/bin/python3"
SELECTED=""

print_help() {
    printf '%s\n' "Usage: ./scripts/dev-setup.sh [--<target>]"
    printf '%s\n' "Create/update the development venv without systemd or user-data changes."
    printf '%s\n' "With no target, install every plugin's private dependencies."
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help; exit 0 ;;
        --) printf '%s\n' "Error: Invalid argument: $1" >&2; exit 1 ;;
        --?*)
            [ -z "$SELECTED" ] || {
                printf '%s\n' "Error: Select at most one target." >&2
                exit 1
            }
            SELECTED="${1#--}"
            ;;
        *) printf '%s\n' "Error: Invalid argument: $1" >&2; exit 1 ;;
    esac
    shift
done

command -v python3 >/dev/null 2>&1 || {
    printf '%s\n' "Error: python3 is not installed." >&2
    exit 1
}
PLUGIN_REQUIREMENTS="$(
    PYTHONPATH="$SCRIPT_DIR/src" python3 -m core.scrapers.tooling.cli requirements
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

[ -d "$SCRIPT_DIR/venv" ] || python3 -m venv "$SCRIPT_DIR/venv"
"$VENV_PYTHON" -m pip install --upgrade pip
"$VENV_PYTHON" -m pip install -r "$SCRIPT_DIR/requirements.txt" \
    -r "$SCRIPT_DIR/requirements-dev.txt"

IFS='
'
for row in $PLUGIN_REQUIREMENTS; do
    target="${row%%	*}"
    requirement="${row#*	}"
    [ -z "$SELECTED" ] || [ "$target" = "$SELECTED" ] || continue
    if [ -n "$requirement" ]; then
        "$VENV_PYTHON" -m pip install -r "$requirement"
    fi
done
IFS="$OLD_IFS"
"$VENV_PYTHON" -m pip check
printf '%s\n' "Development environment is ready."
