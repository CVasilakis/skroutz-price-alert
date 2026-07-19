#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
BASE_DIR="$SCRIPT_DIR"
. "$SCRIPT_DIR/scripts/lib/common.sh"

if [ "$#" -ne 1 ]; then
    printf '%s\n' "Usage: ./scripts/plugin-check.sh --<target>" >&2
    exit 2
fi
case "$1" in
    --?*) target="${1#--}" ;;
    *) printf '%s\n' "Usage: ./scripts/plugin-check.sh --<target>" >&2; exit 2 ;;
esac

plugin_check_python="${SCROOGE_PLUGIN_CHECK_PYTHON:-$BASE_DIR/venv/bin/python3}"
if [ ! -x "$plugin_check_python" ]; then
    printf '%s\n' "Python interpreter not found: $plugin_check_python" >&2
    exit 127
fi
plugin_check_python="$(
    CDPATH='' cd -- "$(dirname -- "$plugin_check_python")" && pwd
)/$(basename -- "$plugin_check_python")"
plugin_venv_parent="$(dirname -- "$(dirname -- "$(dirname -- "$plugin_check_python")")")"

env PYTHONPATH="$BASE_DIR/src" "$plugin_check_python" \
    -m core.scrapers.cli plugin-check "$target"
"$plugin_check_python" -m pytest --no-cov "$BASE_DIR/tests/plugins/$target"
"$plugin_check_python" -m basedpyright --venvpath "$plugin_venv_parent" \
    "$BASE_DIR/src/core/scrapers/$target"
