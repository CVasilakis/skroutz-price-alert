"""SH_MIGRATE scenarios: human rendering of the stable migration TSV protocol."""

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_MIGRATE, "scripts/migrate.sh")

_case("help", "Migration usage, check mode, and debug documentation.", "--help", tags=("help",))

_case(
    "no_op",
    "Existing managed documents are already current; missing documents stay hidden.",
    world=ShellWorld(
        migration_report=(
            "general_config\tgeneral\tcurrent\tconfig/general.json\t",
            "target_config\tskroutz\tmissing\tconfig/skroutz.json\t",
            "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t",
            "reminder_state\tgeneral\tmissing\tstate/general.json\t",
        )
    ),
    tags=("ok",),
)

_case(
    "successful_migration",
    "Outdated general and target documents migrate with retained recovery copies.",
    world=ShellWorld(
        migration_report=(
            "general_config\tgeneral\tmigrated\tconfig/general.json\tv1 to v2",
            "target_config\tskroutz\tmigrated\tconfig/skroutz.json\tframework v1 to v2",
            "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t",
            "recovery\tgeneral\tretained\t/project/state/.migration-recovery.example\t",
        )
    ),
    tags=("ok", "target_config"),
)

_case(
    "check_pending",
    "Check mode identifies a pending migration without modifying the document.",
    "--check",
    world=ShellWorld(
        migration_report=(
            "general_config\tgeneral\tmigrated\tconfig/general.json\tpending v1 to v2",
        ),
        migration_status=1,
    ),
)

_case(
    "partial_failure",
    "One target configuration fails while other document families remain visible.",
    world=ShellWorld(
        migration_report=(
            "general_config\tgeneral\tcurrent\tconfig/general.json\t",
            "target_config\tskroutz\tfailed\tconfig/skroutz.json\t"
            "invalid JSON. Original preserved; compare it with the target example.",
            "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t",
        ),
        migration_status=15,
    ),
    tags=("error", "target_config"),
)

_case(
    "total_failure",
    "Every document family fails and retains its application-authored guidance.",
    world=ShellWorld(
        migration_report=(
            "general_config\tgeneral\tfailed\tconfig/general.json\tgeneral config failed",
            "target_config\tskroutz\tfailed\tconfig/skroutz.json\ttarget config failed",
            "scraper_state\tskroutz\tfailed\tstate/skroutz.json\ttarget state failed",
            "reminder_state\tgeneral\tfailed\tstate/general.json\treminder state failed",
        ),
        migration_status=15,
    ),
    tags=("error", "target_config", "reminder"),
)
