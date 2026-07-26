#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n%s\n' \
        "Usage: ./scripts/dev/plugin-create.sh [-h] <target> --display-name <name>"
    printf '%s\n\n' "       --domain <domain> --url-prefix <prefix>"
    printf '%s\n\n' "Create an additive in-repository scraper plugin scaffold."
    printf '%s\n' "Required arguments:"
    printf '%s\n' "  <target>                  non-reserved snake_case target name"
    printf '%s\n' "  --display-name <name>     user-facing store name"
    printf '%s\n' "  --domain <domain>         supported hostname or IP address"
    printf '%s\n\n' "  --url-prefix <prefix>     URL path prefix beginning with /"
    printf '%s\n' "Optional arguments:"
    printf '%s\n\n' "  -h, --help                show this help message and exit"
}

for argument in "$@"; do
    case "$argument" in
        -h|--help) print_help; exit 0 ;;
    esac
done

require_python_310 python3 "./scripts/dev/setup.sh" || exit 127
PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    exec python3 -m core.scrapers.tooling.scaffold "$@"
