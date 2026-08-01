# Dynamic completion for the public scrooge-alert command.
_scrooge_alert_complete()
{
    local current command help_output in_options line token index help_allowed
    local -a candidates
    current=${COMP_WORDS[COMP_CWORD]}
    candidates=()

    if (( COMP_CWORD == 1 )); then
        help_output=$(scrooge-alert --help 2>/dev/null) || return 0
        while IFS= read -r line; do
            case $line in
                Commands:) in_options=commands; continue ;;
                Options:) in_options=options; continue ;;
                '') in_options=; continue ;;
            esac
            case $in_options in
                commands)
                    read -r token _ <<< "$line"
                    [[ $token == [a-z]* ]] && candidates+=("$token")
                    ;;
                options)
                    read -r token _ <<< "$line"
                    case $token in --help|--version) candidates+=("$token") ;; esac
                    ;;
            esac
        done <<< "$help_output"
    elif (( COMP_CWORD > 1 )); then
        command=${COMP_WORDS[1]}
        case $command in
            ''|[!a-z]*|*[!a-z0-9_-]*) return 0 ;;
        esac
        help_allowed=1
        for ((index = 2; index < COMP_CWORD; index++)); do
            if [[ ${COMP_WORDS[index]} == --* ]]; then
                help_allowed=
                break
            fi
        done
        help_output=$(scrooge-alert "$command" --help 2>/dev/null) || return 0
        in_options=
        while IFS= read -r line; do
            case $line in
                Options:) in_options=1; continue ;;
                '') [[ $in_options ]] && continue ;;
            esac
            [[ $in_options ]] || continue
            read -r token _ <<< "$line"
            if [[ $token == --help && ! $help_allowed ]]; then
                continue
            fi
            [[ $token == --* ]] && candidates+=("$token")
        done <<< "$help_output"
    fi
    mapfile -t COMPREPLY < <(compgen -W "${candidates[*]}" -- "$current")
}
complete -F _scrooge_alert_complete scrooge-alert
