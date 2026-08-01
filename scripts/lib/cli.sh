#!/bin/sh
# User-scoped command and completion lifecycle. Callers source common.sh first.
# shellcheck disable=SC2034  # exported state is consumed by sourcing lifecycle scripts

CLI_MARKER_PREFIX='# scrooge-alert checkout: '
CLI_LAUNCHER_PATH=''
CLI_BASH_COMPLETION_PATH=''
CLI_FISH_COMPLETION_PATH=''
CLI_BASH_ELIGIBLE=0
CLI_FISH_ELIGIBLE=0
CLI_PATH_GUIDANCE=0
CLI_BASH_NOTICE=''
CLI_FISH_NOTICE=''
CLI_ERROR=''

cli_require_home() {
    case "${HOME:-}" in
        /*) ;;
        *) CLI_ERROR='HOME must be an absolute path for user command integration.'; return 1 ;;
    esac
    [ -d "$HOME" ] && [ -r "$HOME" ] && [ -w "$HOME" ] || {
        CLI_ERROR="HOME is not a usable directory: $HOME"
        return 1
    }
}

cli_bash_completion_supported() {
    _cbcs_source="$1"
    # A custom search root may be shell-local or colon-delimited. Without
    # reading profiles, the standard XDG destination cannot then be proven.
    [ -z "${BASH_COMPLETION_USER_DIR:-}" ] || return 1
    command -v bash >/dev/null 2>&1 || return 1
    command -v pkg-config >/dev/null 2>&1 || return 1
    pkg-config --exists bash-completion >/dev/null 2>&1 || return 1
    _cbcs_data_dir="$(pkg-config --variable=datadir bash-completion 2>/dev/null)" || return 1
    case "$_cbcs_data_dir" in
        /*) ;;
        *) return 1 ;;
    esac
    _cbcs_loader="$_cbcs_data_dir/bash-completion/bash_completion"
    [ -f "$_cbcs_loader" ] && [ -r "$_cbcs_loader" ] || return 1
    [ -f "$_cbcs_source" ] && [ ! -L "$_cbcs_source" ] || return 1
    bash -n "$_cbcs_source" >/dev/null 2>&1 || return 1

    _cbcs_workspace="$(
        umask 077
        mktemp -d "${TMPDIR:-/tmp}/scrooge-bash-probe.XXXXXX"
    )" || return 1
    _cbcs_probe_dir="$_cbcs_workspace/bash-completion/completions"
    if ! mkdir -p "$_cbcs_probe_dir" ||
       ! printf '%s\n' 'complete -W probe scrooge-alert-probe' > \
            "$_cbcs_probe_dir/scrooge-alert-probe"; then
        rm -rf "$_cbcs_workspace"
        return 1
    fi
    if XDG_DATA_HOME="$_cbcs_workspace" bash --noprofile --norc -c '
        if ((BASH_VERSINFO[0] < 4 ||
             (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 2))); then
            exit 1
        fi
        type mapfile >/dev/null 2>&1 || exit 1
        . "$1" >/dev/null 2>&1 || exit 1
        if declare -F _comp_load >/dev/null 2>&1; then
            _comp_load scrooge-alert-probe >/dev/null 2>&1
        elif declare -F __load_completion >/dev/null 2>&1; then
            __load_completion scrooge-alert-probe >/dev/null 2>&1
        else
            exit 1
        fi
        complete -p scrooge-alert-probe >/dev/null 2>&1
    ' scrooge-alert-probe "$_cbcs_loader"; then
        _cbcs_status=0
    else
        _cbcs_status=1
    fi
    rm -rf "$_cbcs_workspace"
    return "$_cbcs_status"
}

cli_fish_completion_supported() {
    _cfcs_source="$1"
    _cfcs_dir="$2"
    command -v fish >/dev/null 2>&1 || return 1
    [ -f "$_cfcs_source" ] && [ ! -L "$_cfcs_source" ] || return 1
    fish --no-config -n "$_cfcs_source" >/dev/null 2>&1 || return 1
    # shellcheck disable=SC2016  # Fish, not POSIX sh, expands these variables
    fish --no-config -c \
        'contains -- "$argv[1]" $fish_complete_path' \
        "$_cfcs_dir" >/dev/null 2>&1
}

cli_resolve_paths() {
    cli_require_home || return 1
    CLI_ERROR=''
    CLI_BASH_NOTICE=''
    CLI_FISH_NOTICE=''
    CLI_LAUNCHER_PATH="$HOME/.local/bin/scrooge-alert"
    CLI_BASH_COMPLETION_PATH=''
    CLI_FISH_COMPLETION_PATH=''
    CLI_BASH_ELIGIBLE=0
    CLI_FISH_ELIGIBLE=0

    if [ -n "${XDG_DATA_HOME:-}" ]; then
        case "$XDG_DATA_HOME" in
            /*) _crp_data_home="$XDG_DATA_HOME" ;;
            *) _crp_data_home='' ;;
        esac
    else
        _crp_data_home="$HOME/.local/share"
    fi
    if [ -n "$_crp_data_home" ]; then
        CLI_BASH_COMPLETION_PATH="$_crp_data_home/bash-completion/completions/scrooge-alert"
        CLI_FISH_COMPLETION_PATH="$_crp_data_home/fish/vendor_completions.d/scrooge-alert.fish"
        cli_bash_completion_supported \
            "$BASE_DIR/completions/scrooge-alert.bash" && CLI_BASH_ELIGIBLE=1
        cli_fish_completion_supported \
            "$BASE_DIR/completions/scrooge-alert.fish" \
            "${CLI_FISH_COMPLETION_PATH%/*}" && CLI_FISH_ELIGIBLE=1
    fi
    case ":${PATH:-}:" in
        *":$HOME/.local/bin:"*) CLI_PATH_GUIDANCE=0 ;;
        *) CLI_PATH_GUIDANCE=1 ;;
    esac
}

cli_marker_matches() {
    [ -f "$1" ] && [ ! -L "$1" ] || return 1
    IFS= read -r _cmm_marker < "$1" || return 1
    if [ "$_cmm_marker" = '#!/bin/sh' ]; then
        _cmm_marker="$(sed -n '2p' "$1")"
    fi
    [ "$_cmm_marker" = "$CLI_MARKER_PREFIX$BASE_DIR" ]
}

cli_validate_launcher_destination() {
    if [ -L "$CLI_LAUNCHER_PATH" ]; then
        CLI_ERROR="Managed command destination is a symlink: $CLI_LAUNCHER_PATH"
        return 1
    fi
    if [ -e "$CLI_LAUNCHER_PATH" ] &&
       ! cli_marker_matches "$CLI_LAUNCHER_PATH"; then
        CLI_ERROR="Managed command destination belongs to another installation: $CLI_LAUNCHER_PATH"
        return 1
    fi
}

cli_prepare_completion() {
    _cpc_shell="$1"
    _cpc_path="$2"
    _cpc_eligible="$3"
    [ "$_cpc_eligible" -eq 1 ] || return 1
    if [ -L "$_cpc_path" ] ||
       { [ -e "$_cpc_path" ] && ! cli_marker_matches "$_cpc_path"; }; then
        case "$_cpc_shell" in
            Bash)
                CLI_BASH_NOTICE="Bash completion was preserved and skipped: $_cpc_path"
                CLI_BASH_ELIGIBLE=0
                ;;
            Fish)
                CLI_FISH_NOTICE="Fish completion was preserved and skipped: $_cpc_path"
                CLI_FISH_ELIGIBLE=0
                ;;
        esac
        return 1
    fi
}

cli_preflight_install() {
    cli_resolve_paths || return 1
    cli_validate_launcher_destination || return 1
    if [ -L "$BASE_DIR/scripts/scrooge-alert" ] ||
       [ ! -f "$BASE_DIR/scripts/scrooge-alert" ]; then
        CLI_ERROR="Required command integration file is missing or unsafe: $BASE_DIR/scripts/scrooge-alert"
        return 1
    fi
    cli_prepare_completion Bash "$CLI_BASH_COMPLETION_PATH" \
        "$CLI_BASH_ELIGIBLE" || true
    cli_prepare_completion Fish "$CLI_FISH_COMPLETION_PATH" \
        "$CLI_FISH_ELIGIBLE" || true
}

cli_atomic_launcher() {
    _cal_path="$1"
    _cal_dir="${_cal_path%/*}"
    mkdir -p "$_cal_dir" || return 1
    _cal_tmp="$(mktemp "$_cal_dir/.scrooge-alert.XXXXXX")" || return 1
    _cal_quoted="$(printf '%s' "$BASE_DIR" | sed "s/'/'\\\\''/g")"
    if ! {
        printf '%s\n' '#!/bin/sh'
        printf '%s\n' "$CLI_MARKER_PREFIX$BASE_DIR"
        printf "%s\n" "SCROOGE_ALERT_CHECKOUT='$_cal_quoted'"
        printf '%s\n' 'export SCROOGE_ALERT_CHECKOUT'
        printf "%s\n" "exec '$_cal_quoted/scripts/scrooge-alert' \"\$@\""
    } > "$_cal_tmp" || ! chmod 755 "$_cal_tmp" || ! mv -f "$_cal_tmp" "$_cal_path"; then
        rm -f "$_cal_tmp"
        return 1
    fi
}

cli_atomic_completion() {
    _cac_source="$1"
    _cac_path="$2"
    _cac_dir="${_cac_path%/*}"
    mkdir -p "$_cac_dir" || return 1
    _cac_tmp="$(mktemp "$_cac_dir/.scrooge-alert.XXXXXX")" || return 1
    if ! {
        printf '%s\n' "$CLI_MARKER_PREFIX$BASE_DIR"
        cat "$_cac_source"
    } > "$_cac_tmp" || ! chmod 644 "$_cac_tmp" || ! mv -f "$_cac_tmp" "$_cac_path"; then
        rm -f "$_cac_tmp"
        return 1
    fi
}

cli_reconcile_completion() {
    _crc_shell="$1"
    _crc_source="$2"
    _crc_path="$3"
    _crc_eligible="$4"
    [ -n "$_crc_path" ] || return 0
    if [ "$_crc_eligible" -eq 1 ]; then
        if cli_atomic_completion "$_crc_source" "$_crc_path"; then
            return 0
        fi
        _crc_notice="$_crc_shell completion could not be installed; any prior file was preserved: $_crc_path"
    elif cli_marker_matches "$_crc_path" && ! rm -f "$_crc_path"; then
        _crc_notice="$_crc_shell completion could not be reconciled and was preserved: $_crc_path"
    else
        return 0
    fi
    case "$_crc_shell" in
        Bash) CLI_BASH_NOTICE="$_crc_notice" ;;
        Fish) CLI_FISH_NOTICE="$_crc_notice" ;;
    esac
}

cli_install_artifacts() {
    cli_preflight_install || return 1
    if ! cli_atomic_launcher "$CLI_LAUNCHER_PATH"; then
        CLI_ERROR='Could not install the user command launcher; any prior launcher was preserved.'
        return 1
    fi
    cli_reconcile_completion Bash \
        "$BASE_DIR/completions/scrooge-alert.bash" \
        "$CLI_BASH_COMPLETION_PATH" "$CLI_BASH_ELIGIBLE"
    cli_reconcile_completion Fish \
        "$BASE_DIR/completions/scrooge-alert.fish" \
        "$CLI_FISH_COMPLETION_PATH" "$CLI_FISH_ELIGIBLE"
}

cli_classify_owned() {
    _cco_path="$1"
    [ -n "$_cco_path" ] && cli_marker_matches "$_cco_path"
}

cli_remove_owned() {
    _cro_path="$1"
    _cro_owned="$2"
    [ "$_cro_owned" -eq 1 ] || return 0
    if ! rm -f "$_cro_path"; then
        CLI_ERROR="Could not remove an owned user command artifact: $_cro_path"
        return 1
    fi
}

cli_remove_artifacts() {
    cli_require_home || return 1
    CLI_LAUNCHER_PATH="$HOME/.local/bin/scrooge-alert"
    if [ -n "${XDG_DATA_HOME:-}" ]; then
        case "$XDG_DATA_HOME" in
            /*) _cra_data_home="$XDG_DATA_HOME" ;;
            *) _cra_data_home='' ;;
        esac
    else
        _cra_data_home="$HOME/.local/share"
    fi
    if [ -n "$_cra_data_home" ]; then
        CLI_BASH_COMPLETION_PATH="$_cra_data_home/bash-completion/completions/scrooge-alert"
        CLI_FISH_COMPLETION_PATH="$_cra_data_home/fish/vendor_completions.d/scrooge-alert.fish"
    else
        CLI_BASH_COMPLETION_PATH=''
        CLI_FISH_COMPLETION_PATH=''
    fi

    _cra_launcher_owned=0
    _cra_bash_owned=0
    _cra_fish_owned=0
    cli_classify_owned "$CLI_LAUNCHER_PATH" && _cra_launcher_owned=1
    cli_classify_owned "$CLI_BASH_COMPLETION_PATH" && _cra_bash_owned=1
    cli_classify_owned "$CLI_FISH_COMPLETION_PATH" && _cra_fish_owned=1

    cli_remove_owned "$CLI_BASH_COMPLETION_PATH" "$_cra_bash_owned" || return 1
    cli_remove_owned "$CLI_FISH_COMPLETION_PATH" "$_cra_fish_owned" || return 1
    cli_remove_owned "$CLI_LAUNCHER_PATH" "$_cra_launcher_owned"
}
