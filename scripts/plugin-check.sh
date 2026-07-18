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

exec env PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" \
    -m core.scrapers.cli plugin-check "$target"
