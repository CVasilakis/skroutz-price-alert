#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

if [ "$#" -ne 1 ]; then
    printf '%s\n' "Usage: ./scripts/dev/plugin-check.sh --<target>" >&2
    exit 2
fi
case "$1" in
    --?*) target="${1#--}" ;;
    *) printf '%s\n' "Usage: ./scripts/dev/plugin-check.sh --<target>" >&2; exit 2 ;;
esac

plugin_check_python="${SCROOGE_PLUGIN_CHECK_PYTHON:-$BASE_DIR/venv/bin/python3}"
require_python_310 "$plugin_check_python" "./scripts/dev/setup.sh" || exit 127
plugin_check_python="$(
    CDPATH='' cd -- "$(dirname -- "$plugin_check_python")" && pwd
)/$(basename -- "$plugin_check_python")"

env PYTHONPATH="$BASE_DIR/src" "$plugin_check_python" \
    -m core.scrapers.tooling.cli plugin-check "$target"
"$plugin_check_python" -m pytest --no-cov "$BASE_DIR/tests/plugins/$target"
"$plugin_check_python" -m basedpyright --pythonpath "$plugin_check_python" \
    "$BASE_DIR/src/core/scrapers/plugins/$target"
"$plugin_check_python" -m ruff check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"
"$plugin_check_python" -m ruff format --check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"
