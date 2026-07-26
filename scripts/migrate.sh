#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/lib/preflight.sh"

print_help() {
    printf '\n%s\n\n' "Usage: migrate.sh [-h] [--check]"
    printf '%s\n' "Validate and migrate every known Scrooge Alert JSON document."
    printf '%s\n\n' "With no flag, migrate outdated managed JSON documents in place."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n\n' "  --check           Validate and report without modifying JSON files"
}

for argument in "$@"; do
    case "$argument" in
        -h|--help) print_help; exit 0 ;;
    esac
done

reject_project_venv_symlink || exit 1
require_python_310 "$BASE_DIR/venv/bin/python3" "./install.sh" || exit 1

PYTHONPATH="$BASE_DIR/src" exec "$BASE_DIR/venv/bin/python3" \
    -m core.tooling.migration_cli --root "$BASE_DIR" "$@"
