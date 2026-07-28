#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/lib/preflight.sh"

print_help() {
    printf '\n%s\n\n' "Usage: migrate.sh [-h] [--check] [--debug]"
    printf '%s\n' "Validate and migrate every known Scrooge Alert JSON document."
    printf '%s\n\n' "With no flag, migrate outdated managed JSON documents in place."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --check           Validate and report without modifying JSON files"
    printf '%s\n\n' "  --debug           show underlying command output"
}

HELP_REQUESTED=0
CHECK_MODE=0
MACHINE_MODE=0
INVALID_ARGUMENT=''
for argument in "$@"; do
    case "$argument" in
        -h|--help) HELP_REQUESTED=1 ;;
    esac
done
[ "$HELP_REQUESTED" -eq 0 ] || {
    print_help
    exit 0
}

for argument in "$@"; do
    case "$argument" in
        --check) CHECK_MODE=1 ;;
        --debug)
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
        --machine) MACHINE_MODE=1 ;;
        *)
            [ -n "$INVALID_ARGUMENT" ] || INVALID_ARGUMENT="$argument"
            ;;
    esac
done

migration_task() {
    _mt_kind="$1"
    shift
    case "$_mt_kind" in
        success) _mt_marker='v'; _mt_color="$GREEN" ;;
        failure) _mt_marker='x'; _mt_color="$RED" ;;
        info) _mt_marker='i'; _mt_color="$CYAN" ;;
        warning) _mt_marker='!'; _mt_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _mt_prefix="    ${_mt_color}[${_mt_marker}]${NC} "
    _mt_continuation='        '
    _print_indented_wrapped "$_mt_prefix" "$_mt_continuation" "$@"
}

show_shell_failure() {
    _ssf_status="$1"
    shift
    section_heading success "JSON migration"
    migration_task failure "$@"
    migration_task info "Run ./scripts/migrate.sh --help for usage."
    end_operational_output
    exit "$_ssf_status"
}

if [ -n "$INVALID_ARGUMENT" ]; then
    if [ "$MACHINE_MODE" -eq 1 ]; then
        printf '%s\n' "Error: Invalid argument: $INVALID_ARGUMENT" >&2
        exit 2
    fi
    begin_operational_output
    show_shell_failure 2 "Invalid argument: $INVALID_ARGUMENT"
fi

if [ "$MACHINE_MODE" -eq 0 ]; then
    begin_operational_output
fi

if ! run_action reject_project_venv_symlink; then
    if [ "$MACHINE_MODE" -eq 1 ]; then
        printf '%s\n' "Error: The project Python environment must not be a symlink." >&2
        exit 1
    fi
    section_heading success "Migration preflight"
    migration_task failure "The project Python environment must not be a symlink."
    migration_task info "Remove the symlink, then run ./scripts/dev/setup.sh or ./install.sh."
    end_operational_output
    exit 1
fi
if ! run_action require_python_310 "$BASE_DIR/venv/bin/python3" "./install.sh"; then
    if [ "$MACHINE_MODE" -eq 1 ]; then
        printf '%s\n' "Error: Python 3.10 or newer is required. Run ./install.sh." >&2
        exit 1
    fi
    section_heading success "Migration preflight"
    migration_task failure "Python 3.10 or newer is required."
    migration_task info "Run ./install.sh, then retry the migration."
    end_operational_output
    exit 1
fi

# shellcheck disable=SC2329  # invoked indirectly by run_captured
run_migration_engine() {
    set -- -m core.tooling.migration_cli --root "$BASE_DIR" --machine
    [ "$CHECK_MODE" -eq 0 ] || set -- "$@" --check
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" "$@"
}

if run_captured run_migration_engine; then
    MIGRATION_STATUS=0
else
    MIGRATION_STATUS=$?
fi
MIGRATION_REPORT="$CAPTURED_COMMAND_OUTPUT"

if [ "$MACHINE_MODE" -eq 1 ]; then
    [ -z "$MIGRATION_REPORT" ] || printf '%s\n' "$MIGRATION_REPORT"
    exit "$MIGRATION_STATUS"
fi

render_family() {
    _rf_family="$1"
    _rf_heading="$2"
    _rf_has_rows=0
    _rf_tab="$(printf '\t')"
    _rf_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only TSV row iteration
    for _rf_row in $MIGRATION_REPORT; do
        _rf_row_family="${_rf_row%%"$_rf_tab"*}"
        [ "$_rf_row_family" = "$_rf_family" ] || continue
        _rf_rest="${_rf_row#*"$_rf_tab"}"
        _rf_rest="${_rf_rest#*"$_rf_tab"}"
        _rf_result="${_rf_rest%%"$_rf_tab"*}"
        _rf_rest="${_rf_rest#*"$_rf_tab"}"
        _rf_path="${_rf_rest%%"$_rf_tab"*}"
        _rf_detail="${_rf_rest#*"$_rf_tab"}"
        [ "$_rf_result" = missing ] && continue
        if [ "$_rf_has_rows" -eq 0 ]; then
            [ "$VISIBLE_SECTION_COUNT" -eq 0 ] || printf '\n'
            section_heading success "$_rf_heading"
            VISIBLE_SECTION_COUNT=$((VISIBLE_SECTION_COUNT + 1))
            _rf_has_rows=1
        fi
        case "$_rf_result" in
            current)
                migration_task success "$_rf_path is current."
                ;;
            migrated)
                if [ "$CHECK_MODE" -eq 1 ]; then
                    _rf_detail="${_rf_detail#pending }"
                    migration_task info "$_rf_path requires migration: $_rf_detail."
                else
                    migration_task success "$_rf_path migrated: $_rf_detail."
                fi
                ;;
            failed)
                migration_task failure "$_rf_path: $_rf_detail"
                ;;
        esac
    done
    IFS="$_rf_old_ifs"
}

VISIBLE_SECTION_COUNT=0
render_family general_config "General configuration"
render_family target_config "Target configuration"
render_family scraper_state "Target state"
render_family reminder_state "Reminder state"

MIGRATION_TAB="$(printf '\t')"
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # deliberate newline-only TSV row iteration
for migration_row in $MIGRATION_REPORT; do
    migration_family="${migration_row%%"$MIGRATION_TAB"*}"
    [ "$migration_family" = recovery ] || continue
    migration_rest="${migration_row#*"$MIGRATION_TAB"}"
    migration_rest="${migration_rest#*"$MIGRATION_TAB"}"
    migration_result="${migration_rest%%"$MIGRATION_TAB"*}"
    migration_rest="${migration_rest#*"$MIGRATION_TAB"}"
    migration_path="${migration_rest%%"$MIGRATION_TAB"*}"
    [ "$migration_result" = retained ] || continue
    [ "$VISIBLE_SECTION_COUNT" -eq 0 ] || printf '\n'
    section_heading warning "Recovery copies"
    migration_task warning "Retained at $migration_path."
    VISIBLE_SECTION_COUNT=$((VISIBLE_SECTION_COUNT + 1))
done
IFS="$OLD_IFS"

if [ "$VISIBLE_SECTION_COUNT" -eq 0 ]; then
    section_heading success "JSON migration"
    if [ "$MIGRATION_STATUS" -eq 0 ]; then
        migration_task info "No existing managed JSON documents were found."
    else
        migration_task failure "Migration could not start."
        migration_task info "Retry with ./scripts/migrate.sh --debug for underlying diagnostics."
    fi
fi

end_operational_output
exit "$MIGRATION_STATUS"
