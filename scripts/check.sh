#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"
CHECK_MODE="${1:-full}"

print_help() {
    printf '%s\n' "Usage: ./scripts/check.sh [full|static|shell|tests]"
    printf '%s\n' "Run the project's non-mutating local/CI acceptance checks."
    printf '%s\n' "With no flag, run the complete local pre-push gate."
}

case "$CHECK_MODE" in
    -h|--help) print_help; exit 0 ;;
    full|static|shell|tests) ;;
    *) printf '%s\n' "Error: Invalid argument: $CHECK_MODE" >&2; print_help >&2; exit 2 ;;
esac
[ "$#" -le 1 ] || {
    printf '%s\n' "Error: Select at most one check mode." >&2
    exit 2
}

CHECK_PYTHON="${SCROOGE_CHECK_PYTHON:-$PROJECT_ROOT/venv/bin/python3}"

require_python() {
    case "$CHECK_PYTHON" in
        */*) [ -x "$CHECK_PYTHON" ] ;;
        *) command -v "$CHECK_PYTHON" >/dev/null 2>&1 ;;
    esac || {
        printf '%s\n' "Python interpreter not found: $CHECK_PYTHON" >&2
        printf '%s\n' "Run ./scripts/dev-setup.sh first." >&2
        exit 127
    }
}

run_static() {
    require_python
    "$CHECK_PYTHON" -m ruff check "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"
    "$CHECK_PYTHON" -m ruff format --check "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"
    (
        cd "$PROJECT_ROOT"
        "$CHECK_PYTHON" -m basedpyright src
    )
}

run_shell() {
    shellcheck_binary="${SCROOGE_SHELLCHECK:-$PROJECT_ROOT/venv/bin/shellcheck}"
    if [ ! -x "$shellcheck_binary" ]; then
        shellcheck_binary="$(command -v shellcheck || true)"
    fi
    [ -n "$shellcheck_binary" ] || {
        printf '%s\n' "Shellcheck is not available. Run ./scripts/dev-setup.sh first." >&2
        exit 127
    }
    (
        cd "$PROJECT_ROOT"
        git ls-files -z -- '*.sh' |
            xargs -0 "$shellcheck_binary" -x --exclude=SC2086,SC2046
    )
}

run_tests() {
    require_python
    (
        cd "$PROJECT_ROOT"
        "$CHECK_PYTHON" -m pytest
    )
}

case "$CHECK_MODE" in
    static) run_static ;;
    shell) run_shell ;;
    tests) run_tests ;;
    full)
        run_static
        run_shell
        "$CHECK_PYTHON" -m pip check
        run_tests
        ;;
esac
