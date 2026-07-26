#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/lib/preflight.sh"

reject_project_venv_symlink || exit 1
require_python_310 "$BASE_DIR/venv/bin/python3" "./install.sh" || exit 1

PYTHONPATH="$BASE_DIR/src" exec "$BASE_DIR/venv/bin/python3" \
    -m core.tooling.migration_cli --root "$BASE_DIR" "$@"
