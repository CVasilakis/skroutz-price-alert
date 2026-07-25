#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"
CHECK_MODE="${1:-full}"

print_help() {
    printf '%s\n' "Usage: ./scripts/dev/check.sh [full|static|shell|tests]"
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
    if [ -z "${SCROOGE_CHECK_PYTHON:-}" ]; then
        reject_project_venv_symlink || exit 127
    fi
    require_python_310 "$CHECK_PYTHON" "./scripts/dev/setup.sh" || exit 127
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
    require_python
    shellcheck_binary="${SCROOGE_SHELLCHECK:-$PROJECT_ROOT/venv/bin/shellcheck}"
    if [ ! -x "$shellcheck_binary" ]; then
        shellcheck_binary="$(command -v shellcheck || true)"
    fi
    [ -n "$shellcheck_binary" ] || {
        printf '%s\n' "Shellcheck is not available. Run ./scripts/dev/setup.sh first." >&2
        exit 127
    }
    (
        cd "$PROJECT_ROOT"
        if [ "$(git rev-parse --is-inside-work-tree 2>/dev/null || true)" != "true" ]; then
            printf '%s\n' "Error: Shell checks require a Git worktree." >&2
            exit 1
        fi
        dash_binary="$(command -v dash || true)"
        if [ -n "${CI:-}" ] && [ -z "$dash_binary" ]; then
            printf '%s\n' "Error: dash is required for shell syntax checks in CI." >&2
            exit 127
        fi

        # The child sh, not this parent script, must expand positional values.
        # shellcheck disable=SC2016
        git ls-files --cached --others --exclude-standard -z |
            xargs -0 -n 1 sh -c '
                set -eu
                shellcheck_command="$1"
                dash_command="$2"
                path="${3:-}"
                [ -n "$path" ] && [ -f "$path" ] || exit 0
                case "$path" in
                    *.sh) ;;
                    *)
                        IFS= read -r first_line < "$path" || first_line=""
                        case "$first_line" in
                            "#!"*"/sh"*|"#!"*" env sh"*|"#!"*"/dash"*|"#!"*" env dash"*) ;;
                            *) exit 0 ;;
                        esac
                        ;;
                esac
                "$shellcheck_command" -x "$path"
                sh -n "$path"
                [ -z "$dash_command" ] || "$dash_command" -n "$path"
            ' sh "$shellcheck_binary" "$dash_binary"
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
