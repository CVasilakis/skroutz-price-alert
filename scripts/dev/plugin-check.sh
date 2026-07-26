#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n%s\n\n' "Usage: ./scripts/dev/plugin-check.sh [-h] --<target>"
    printf '%s\n\n' "Verify one plugin against its source, tests, and private dependencies."
    printf '%s\n' "Required arguments:"
    printf '%s\n\n' "  --<target>        target plugin to verify (for example, --skroutz)"
    printf '%s\n' "Optional arguments:"
    printf '%s\n\n' "  -h, --help        show this help message and exit"
}

case "${1:-}" in
    -h|--help) print_help; exit 0 ;;
esac

if [ "$#" -ne 1 ]; then
    printf '%s\n' "Usage: ./scripts/dev/plugin-check.sh --<target>" >&2
    exit 2
fi
case "$1" in
    --?*) target="${1#--}" ;;
    *) printf '%s\n' "Usage: ./scripts/dev/plugin-check.sh --<target>" >&2; exit 2 ;;
esac

if [ -z "${SCROOGE_PLUGIN_CHECK_PYTHON:-}" ]; then
    reject_project_venv_symlink || exit 1
fi
plugin_check_python="${SCROOGE_PLUGIN_CHECK_PYTHON:-$BASE_DIR/venv/bin/python3}"
require_python_310 "$plugin_check_python" "./scripts/dev/setup.sh" || exit 127
plugin_check_python="$(
    CDPATH='' cd -- "$(dirname -- "$plugin_check_python")" && pwd
)/$(basename -- "$plugin_check_python")"
plugin_check_venv_dir="$(dirname -- "$(dirname -- "$plugin_check_python")")"
[ "$(basename -- "$plugin_check_venv_dir")" = "venv" ] || {
    printf '%s\n' \
        "Error: The plugin-check Python must belong to a virtual environment named venv." >&2
    exit 2
}
plugin_check_venv_parent="$(dirname -- "$plugin_check_venv_dir")"

env PYTHONPATH="$BASE_DIR/src" "$plugin_check_python" \
    -m core.scrapers.tooling.cli plugin-check "$target"
"$plugin_check_python" -m pytest --no-cov "$BASE_DIR/tests/plugins/$target"
"$plugin_check_python" -m basedpyright --venvpath "$plugin_check_venv_parent" \
    "$BASE_DIR/src/core/scrapers/plugins/$target"
"$plugin_check_python" -m ruff check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"
"$plugin_check_python" -m ruff format --check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"
