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
    command -v bash >/dev/null 2>&1 || return 1
    if command -v pkg-config >/dev/null 2>&1 &&
       pkg-config --exists bash-completion >/dev/null 2>&1; then
        return 0
    fi
    for _cbcs_loader in \
        /usr/share/bash-completion/bash_completion \
        /usr/local/share/bash-completion/bash_completion \
        /etc/bash_completion; do
        [ ! -r "$_cbcs_loader" ] || return 0
    done
    return 1
}

cli_resolve_paths() {
    cli_require_home || return 1
    CLI_LAUNCHER_PATH="$HOME/.local/bin/scrooge-alert"
    CLI_BASH_ELIGIBLE=0
    CLI_FISH_ELIGIBLE=0
    cli_bash_completion_supported && CLI_BASH_ELIGIBLE=1
    command -v fish >/dev/null 2>&1 && CLI_FISH_ELIGIBLE=1

    if [ -n "${XDG_DATA_HOME:-}" ]; then
        case "$XDG_DATA_HOME" in
            /*) _crp_data_home="$XDG_DATA_HOME" ;;
            *)
                if [ "$CLI_BASH_ELIGIBLE" -eq 1 ] ||
                   [ "$CLI_FISH_ELIGIBLE" -eq 1 ]; then
                    CLI_ERROR='XDG_DATA_HOME must be absolute for shell completion integration.'
                    return 1
                fi
                _crp_data_home=''
                ;;
        esac
    else
        _crp_data_home="$HOME/.local/share"
    fi
    if [ -n "$_crp_data_home" ]; then
        CLI_BASH_COMPLETION_PATH="$_crp_data_home/bash-completion/completions/scrooge-alert"
        CLI_FISH_COMPLETION_PATH="$_crp_data_home/fish/vendor_completions.d/scrooge-alert.fish"
    else
        CLI_BASH_COMPLETION_PATH=''
        CLI_FISH_COMPLETION_PATH=''
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

cli_validate_write_destination() {
    _cvwd_path="$1"
    if [ -L "$_cvwd_path" ]; then
        CLI_ERROR="Managed command destination is a symlink: $_cvwd_path"
        return 1
    fi
    if [ -e "$_cvwd_path" ] && ! cli_marker_matches "$_cvwd_path"; then
        CLI_ERROR="Managed command destination belongs to another installation: $_cvwd_path"
        return 1
    fi
}

cli_preflight_install() {
    cli_resolve_paths || return 1
    cli_validate_write_destination "$CLI_LAUNCHER_PATH" || return 1
    if [ "$CLI_BASH_ELIGIBLE" -eq 1 ]; then
        cli_validate_write_destination "$CLI_BASH_COMPLETION_PATH" || return 1
    elif [ -n "$CLI_BASH_COMPLETION_PATH" ] &&
         [ -L "$CLI_BASH_COMPLETION_PATH" ]; then
        CLI_ERROR="Managed completion destination is a symlink: $CLI_BASH_COMPLETION_PATH"
        return 1
    fi
    if [ "$CLI_FISH_ELIGIBLE" -eq 1 ]; then
        cli_validate_write_destination "$CLI_FISH_COMPLETION_PATH" || return 1
    elif [ -n "$CLI_FISH_COMPLETION_PATH" ] &&
         [ -L "$CLI_FISH_COMPLETION_PATH" ]; then
        CLI_ERROR="Managed completion destination is a symlink: $CLI_FISH_COMPLETION_PATH"
        return 1
    fi
    for _cpi_source in \
        "$BASE_DIR/scripts/scrooge-alert" \
        "$BASE_DIR/completions/scrooge-alert.bash" \
        "$BASE_DIR/completions/scrooge-alert.fish"; do
        if [ -L "$_cpi_source" ] || [ ! -f "$_cpi_source" ]; then
            CLI_ERROR="Required command integration file is missing or unsafe: $_cpi_source"
            return 1
        fi
    done
}

cli_atomic_launcher() {
    _cal_path="$1"
    _cal_dir="${_cal_path%/*}"
    mkdir -p "$_cal_dir" || return 1
    _cal_tmp="$(mktemp "$_cal_dir/.scrooge-alert.XXXXXX")" || return 1
    _cal_quoted="$(printf '%s' "$BASE_DIR" | sed "s/'/'\\\\''/g")"
    if ! {
        printf '%s\n' "#!/bin/sh"
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

cli_snapshot_file() {
    _csf_path="$1"
    _csf_backup="$2"
    if [ -f "$_csf_path" ] && [ ! -L "$_csf_path" ]; then
        cp -p "$_csf_path" "$_csf_backup"
        return 0
    fi
    return 1
}

cli_restore_file() {
    _crf_path="$1"
    _crf_backup="$2"
    _crf_existed="$3"
    if [ "$_crf_existed" -eq 1 ]; then
        mkdir -p "${_crf_path%/*}" && cp -p "$_crf_backup" "$_crf_path"
    else
        rm -f "$_crf_path"
    fi
}

cli_install_artifacts() {
    cli_preflight_install || return 1
    _cia_workspace="$(mktemp -d "${TMPDIR:-/tmp}/scrooge-cli.XXXXXX")" || {
        CLI_ERROR='Could not create a command integration recovery workspace.'
        return 1
    }
    _cia_launcher_old=0
    _cia_bash_old=0
    _cia_fish_old=0
    cli_snapshot_file "$CLI_LAUNCHER_PATH" "$_cia_workspace/launcher" &&
        _cia_launcher_old=1
    if [ -n "$CLI_BASH_COMPLETION_PATH" ]; then
        cli_snapshot_file "$CLI_BASH_COMPLETION_PATH" "$_cia_workspace/bash" &&
            _cia_bash_old=1
    fi
    if [ -n "$CLI_FISH_COMPLETION_PATH" ]; then
        cli_snapshot_file "$CLI_FISH_COMPLETION_PATH" "$_cia_workspace/fish" &&
            _cia_fish_old=1
    fi

    _cia_failed=0
    cli_atomic_launcher "$CLI_LAUNCHER_PATH" || _cia_failed=1
    if [ "$_cia_failed" -eq 0 ] && [ "$CLI_BASH_ELIGIBLE" -eq 1 ]; then
        cli_atomic_completion "$BASE_DIR/completions/scrooge-alert.bash" \
            "$CLI_BASH_COMPLETION_PATH" || _cia_failed=1
    elif [ "$_cia_failed" -eq 0 ] && [ -n "$CLI_BASH_COMPLETION_PATH" ] &&
         cli_marker_matches "$CLI_BASH_COMPLETION_PATH"; then
        rm -f "$CLI_BASH_COMPLETION_PATH" || _cia_failed=1
    fi
    if [ "$_cia_failed" -eq 0 ] && [ "$CLI_FISH_ELIGIBLE" -eq 1 ]; then
        cli_atomic_completion "$BASE_DIR/completions/scrooge-alert.fish" \
            "$CLI_FISH_COMPLETION_PATH" || _cia_failed=1
    elif [ "$_cia_failed" -eq 0 ] && [ -n "$CLI_FISH_COMPLETION_PATH" ] &&
         cli_marker_matches "$CLI_FISH_COMPLETION_PATH"; then
        rm -f "$CLI_FISH_COMPLETION_PATH" || _cia_failed=1
    fi

    if [ "$_cia_failed" -ne 0 ]; then
        cli_restore_file "$CLI_LAUNCHER_PATH" "$_cia_workspace/launcher" \
            "$_cia_launcher_old" || true
        [ -z "$CLI_BASH_COMPLETION_PATH" ] ||
            cli_restore_file "$CLI_BASH_COMPLETION_PATH" "$_cia_workspace/bash" \
                "$_cia_bash_old" || true
        [ -z "$CLI_FISH_COMPLETION_PATH" ] ||
            cli_restore_file "$CLI_FISH_COMPLETION_PATH" "$_cia_workspace/fish" \
                "$_cia_fish_old" || true
        rm -rf "$_cia_workspace"
        CLI_ERROR='Could not install the user command artifacts; prior artifacts were restored.'
        return 1
    fi
    rm -rf "$_cia_workspace"
}

cli_remove_one() {
    _cro_path="$1"
    [ -n "$_cro_path" ] || return 0
    if [ -L "$_cro_path" ]; then
        rm -f "$_cro_path"
    elif [ ! -e "$_cro_path" ]; then
        return 0
    elif cli_marker_matches "$_cro_path"; then
        rm -f "$_cro_path"
    else
        CLI_ERROR="Refusing to remove an artifact owned by another installation: $_cro_path"
        return 1
    fi
}

cli_remove_artifacts() {
    cli_require_home || return 1
    CLI_LAUNCHER_PATH="$HOME/.local/bin/scrooge-alert"
    if [ -n "${XDG_DATA_HOME:-}" ]; then
        case "$XDG_DATA_HOME" in
            /*) _cra_data_home="$XDG_DATA_HOME" ;;
            *) CLI_ERROR='XDG_DATA_HOME must be absolute to remove shell completions safely.'; return 1 ;;
        esac
    else
        _cra_data_home="$HOME/.local/share"
    fi
    CLI_BASH_COMPLETION_PATH="$_cra_data_home/bash-completion/completions/scrooge-alert"
    CLI_FISH_COMPLETION_PATH="$_cra_data_home/fish/vendor_completions.d/scrooge-alert.fish"
    cli_remove_one "$CLI_LAUNCHER_PATH" || return 1
    cli_remove_one "$CLI_BASH_COMPLETION_PATH" || return 1
    cli_remove_one "$CLI_FISH_COMPLETION_PATH" || return 1
}
