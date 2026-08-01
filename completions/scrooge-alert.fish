# Dynamic completion for the public scrooge-alert command.
function __scrooge_alert_top
    scrooge-alert --help 2>/dev/null | awk '
        /^Commands:$/ { section="commands"; next }
        /^Options:$/ { section="options"; next }
        /^$/ { section=""; next }
        section == "commands" { print $1 "\t" substr($0, index($0, $2)) }
        section == "options" && ($1 == "--help" || $1 == "--version") {
            print $1 "\t" substr($0, index($0, $2))
        }'
end

function __scrooge_alert_command
    set -l words (commandline -opc)
    test (count $words) -ge 2; or return 1
    string match -rq -- '^[a-z][a-z0-9_-]*$' $words[2]; or return 1
    scrooge-alert $words[2] --help >/dev/null 2>&1; or return 1
    echo $words[2]
end

function __scrooge_alert_options
    set -l command (__scrooge_alert_command); or return
    set -l hide_help 0
    set -l words (commandline -opc)
    if test (count $words) -gt 2
        for word in $words[3..-1]
            if string match -q -- '--*' $word
                set hide_help 1
                break
            end
        end
    end
    scrooge-alert $command --help 2>/dev/null | awk -v hide_help=$hide_help '
        /^Options:$/ { options=1; next }
        options && $1 ~ /^--/ && !(hide_help && $1 == "--help") {
            print $1 "\t" substr($0, index($0, $2))
        }'
end

complete -c scrooge-alert -f -n 'not __scrooge_alert_command' -a '(__scrooge_alert_top)'
complete -c scrooge-alert -f -n '__scrooge_alert_command' -a '(__scrooge_alert_options)'
