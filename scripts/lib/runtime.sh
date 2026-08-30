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

# Never returns: exec replaces this shell with the Python entry point.
exec_runtime_entrypoint() {
    _ere_entrypoint="$1"
    shift
    exec "$VENV_PYTHON" "$BASE_DIR/src/core/$_ere_entrypoint" "$@"
}

# --- Shared target-flag plumbing for the runtime wrappers (run.sh, status.sh) ---
# Each wrapper owns its own help text and case ladder; these helpers own the
# user-facing failure wording and the POSIX argument-forwarding mechanics so the
# two wrappers cannot drift apart. Requires common.sh (catalog_cli,
# is_valid_target, stream_contains).

runtime_catalog_failure() {
    if _rcf_output="$(catalog_cli diagnose 2>&1)"; then
        _rcf_recovery="The target catalog is readable now; retry the command."
    else
        _rcf_recovery="Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry."
    fi
    _rcf_detail="$(
        printf '%s\n' "$_rcf_output" |
            awk 'NF { sub(/^[[:space:]]+/, ""); print; exit }'
    )"
    runtime_failure \
        "Run preflight" \
        "The target catalog could not be loaded." \
        "$_rcf_detail" \
        "$_rcf_recovery"
}

RUNTIME_FORWARD_COUNT=0

runtime_forward_arg() {
    RUNTIME_FORWARD_COUNT=$((RUNTIME_FORWARD_COUNT + 1))
    # Values reaching this helper are fixed built-ins or validated target flags.
    eval "RUNTIME_FORWARD_$RUNTIME_FORWARD_COUNT=\$1"
}

# runtime_target_flag <command> <flag> <known targets> <catalog available>
# Validates one '--<target>' flag against the caller's known-target stream and
# queues it for forwarding. Exits with the shared diagnosis on any rejection.
runtime_target_flag() {
    _rtf_command="$1"
    _rtf_flag="$2"
    _rtf_known="$3"
    _rtf_catalog_available="$4"
    _rtf_name="${_rtf_flag#--}"
    if ! is_valid_target "$_rtf_name"; then
        runtime_argument_failure "$_rtf_command" \
            "Invalid target flag: $_rtf_flag (expected --<snake_case target>)."
    fi
    if stream_contains "$_rtf_name" "$_rtf_known"; then
        runtime_forward_arg "$_rtf_flag"
        return 0
    fi
    # An unknown flag is only meaningful when the catalog itself loaded.
    if [ "$_rtf_catalog_available" -eq 0 ]; then
        runtime_catalog_failure
    fi
    runtime_argument_failure "$_rtf_command" "Unknown target flag: $_rtf_flag."
}

# POSIX sh has no arrays: re-expand the numbered queue positionally.
_runtime_forward_expand() {
    _rfe_entrypoint="$1"
    _rfe_index="$2"
    shift 2
    if [ "$_rfe_index" -gt "$RUNTIME_FORWARD_COUNT" ]; then
        # Base case: exec_runtime_entrypoint never returns, which is what ends the
        # walk. A "return" here would be dead code, but anything that did resume
        # would read one past the queue and trip set -u on RUNTIME_FORWARD_<count+1>.
        exec_runtime_entrypoint "$_rfe_entrypoint" "$@"
    fi
    _rfe_value=''
    eval "_rfe_value=\${RUNTIME_FORWARD_$_rfe_index}"
    _runtime_forward_expand "$_rfe_entrypoint" "$((_rfe_index + 1))" "$@" "$_rfe_value"
}

runtime_exec_forwarded() {
    _runtime_forward_expand "$1" 1
}
