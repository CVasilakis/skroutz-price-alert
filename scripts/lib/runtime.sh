#!/bin/sh
# Shared runtime-entrypoint preflight. Callers define BASE_DIR and source common.sh first.

VENV_PYTHON="$BASE_DIR/venv/bin/python3"

runtime_failure() {
    _rf_section="$1"
    _rf_message="$2"
    _rf_detail="${3:-}"
    _rf_recovery="${4:-}"

    begin_operational_output
    section_heading success "$_rf_section"
    task_status failure "$_rf_message"
    if [ -n "$_rf_detail" ]; then
        task_status info "$_rf_detail"
    fi
    if [ -n "$_rf_recovery" ]; then
        task_status warning "$_rf_recovery"
    fi
    end_operational_output
    exit 1
}

runtime_argument_failure() {
    _raf_command="$1"
    _raf_message="$2"
    runtime_failure \
        "Run arguments" \
        "$_raf_message" \
        "" \
        "Run $(command_text "./scrooge-alert $_raf_command --help") to view supported options."
}

require_runtime_python() {
    if [ -L "$BASE_DIR/venv" ]; then
        runtime_failure \
            "Run preflight" \
            "The project venv path must be a project-owned directory, not a symlink." \
            "" \
            "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')."
    fi
    if [ ! -x "$VENV_PYTHON" ]; then
        runtime_failure \
            "Run preflight" \
            "The project Python environment is missing or unusable." \
            "" \
            "Run $(command_text './scrooge-alert install'), then retry."
    fi
    if ! RUNTIME_PYTHON_VERSION="$(
        "$VENV_PYTHON" -c \
            'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null
    )"; then
        runtime_failure \
            "Run preflight" \
            "The project Python environment could not be executed." \
            "" \
            "Run $(command_text './scrooge-alert install'), then retry."
    fi
    if ! "$VENV_PYTHON" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1; then
        [ -n "$RUNTIME_PYTHON_VERSION" ] || RUNTIME_PYTHON_VERSION="unknown"
        runtime_failure \
            "Run preflight" \
            "Python $RUNTIME_PYTHON_VERSION is unsupported; Python 3.10 or newer is required." \
            "" \
            "Install a supported Python, run $(command_text './scrooge-alert install'), then retry."
    fi
}

exec_runtime_entrypoint() {
    _ere_entrypoint="$1"
    shift
    exec "$VENV_PYTHON" "$BASE_DIR/src/core/$_ere_entrypoint" "$@"
}
