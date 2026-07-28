"""Behavioral update.sh transcripts with Git, systemd, pip, and network shimmed."""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_UPDATE, "update.sh")

_BASE = ShellWorld(
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
    config_files=("skroutz.json", "general.json"),
)

_case("help", "The update usage text.", "--help", world=_BASE, tags=("help",))
_case(
    "help_with_debug",
    "Debug remains compatible with help in either position.",
    "--debug",
    "--help",
    world=_BASE,
    tags=("help",),
)
_case(
    "clean_happy_path",
    "Clean main fast-forwards and restores its timer.",
    world=_BASE,
    tags=("ok",),
)
_case(
    "clean_happy_path_debug",
    "Debug streams nested Git, migration, package, and systemd output.",
    "--debug",
    world=replace(
        _BASE,
        git_stdout="debug git stdout",
        git_stderr="debug git stderr",
        migration_stderr="debug migration stderr",
        pip_stdout="debug pip stdout",
        pip_stderr="debug pip stderr",
        systemctl_stdout="debug systemctl stdout",
        systemctl_stderr="debug systemctl stderr",
    ),
    tags=("ok",),
)
_case("invalid_argument", "A non-help argument is rejected.", "foo", world=_BASE, tags=("error",))
_case(
    "extra_after_help",
    "Help must be the only argument.",
    "--help",
    "extra",
    world=_BASE,
    tags=("error",),
)
_case(
    "no_git",
    "Git is required before any mutation.",
    world=replace(_BASE, tools="no-git"),
    tags=("error",),
)
_case(
    "no_worktree",
    "A non-worktree checkout is refused.",
    world=replace(_BASE, git_worktree=False),
    tags=("error",),
)
_case(
    "missing_origin",
    "A missing origin remote is refused.",
    world=replace(_BASE, git_origin=False),
    tags=("error",),
)
_case(
    "pruned_origin_main",
    "A valid origin can recreate a missing local origin/main ref by fetching.",
    world=replace(_BASE, git_origin_ref=False),
)
_case(
    "no_systemctl",
    "A systemd-less host is refused before Git changes.",
    world=replace(_BASE, tools="no-systemctl"),
    tags=("error",),
)
_case(
    "no_installed_units",
    "Updating cannot infer a selection when no units exist.",
    world=ShellWorld(config_files=("general.json",)),
    tags=("error",),
)
_case(
    "dirty_tree",
    "Dirty tracked or nonignored files are refused without a prompt.",
    world=replace(_BASE, git_dirty=True),
    tags=("error",),
)
_case(
    "wrong_branch",
    "The updater never switches branches implicitly.",
    world=replace(_BASE, git_branch="beta"),
    tags=("error",),
)
_case(
    "ahead_of_origin",
    "Unpublished local commits are protected.",
    world=replace(_BASE, git_relation="ahead"),
    tags=("error",),
)
_case(
    "diverged",
    "Diverged history requires manual reconciliation.",
    world=replace(_BASE, git_relation="diverged"),
    tags=("error",),
)
_case(
    "fetch_fails",
    "Fetch fails before systemd mutation.",
    world=replace(_BASE, git_fail=("fetch",)),
    tags=("error",),
)
_case(
    "fetched_installer_missing",
    "The fetched commit must contain all updater dependencies.",
    world=replace(_BASE, fetched_paths_valid=False),
    tags=("error",),
)
_case(
    "quiesce_fails_before_advance",
    "A scraper that cannot quiesce aborts before source advancement.",
    world=replace(_BASE, activating_services=("skroutz",), systemctl_fail=("stop",)),
    tags=("error",),
)
_case(
    "fast_forward_fails",
    "A fast-forward failure restores the prior enabled and active timer state.",
    world=replace(_BASE, git_fail=("merge",)),
    tags=("error",),
)
_case(
    "interrupted_after_advance",
    "A signal during source advancement leaves deterministic recovery guidance.",
    world=replace(_BASE, git_signal="merge"),
    tags=("error", "interrupt"),
)
_case(
    "interrupted_during_activation",
    "A signal during timer restoration disables the whole selected set.",
    world=replace(_BASE, systemctl_signal="start"),
    tags=("error", "interrupt"),
)
_case(
    "activation_failure_disables_all",
    "A normal restoration failure also disables the whole selected set.",
    world=ShellWorld(
        plugins=("alpha", "beta"),
        installed_timers=("alpha", "beta"),
        installed_services=("alpha", "beta"),
        enabled_timers=("alpha", "beta"),
        active_timers=("alpha", "beta"),
        systemctl_fail=("start",),
        systemctl_fail_target="beta",
        config_files=("alpha.json", "beta.json", "general.json"),
    ),
    tags=("error",),
)
_case(
    "install_fails_during_update",
    "A post-update dependency failure leaves timers disabled.",
    world=replace(_BASE, pip_fail="upgrade"),
    tags=("error",),
)
_case(
    "partial_invalid_config",
    "The source update reprovisions a healthy target and preserves a malformed target.",
    world=ShellWorld(
        plugins=("alpha", "beta"),
        installed_timers=("alpha", "beta"),
        installed_services=("alpha", "beta"),
        enabled_timers=("alpha", "beta"),
        active_timers=("alpha", "beta"),
        config_files=("alpha.json", "beta.json", "general.json"),
        schedule_errors={"beta": "Remove unsupported keys from `config/beta.json`."},
    ),
    tags=("error", "target_config"),
)
_case(
    "target_config_migration_failure",
    "A target-config migration failure isolates that target during reprovisioning.",
    world=replace(
        _BASE,
        migration_report=(
            "target_config\tskroutz\tfailed\tconfig/skroutz.json\tinvalid legacy config",
            "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t",
        ),
        migration_status=15,
    ),
    tags=("error", "target_config", "system"),
)
_case(
    "scraper_state_migration_failure",
    "A scraper-state migration failure isolates its target and reports storage failure.",
    world=replace(
        _BASE,
        migration_report=(
            "target_config\tskroutz\tcurrent\tconfig/skroutz.json\t",
            "scraper_state\tskroutz\tfailed\tstate/skroutz.json\tinvalid legacy state",
        ),
        migration_status=19,
    ),
    tags=("error", "system"),
)
_case(
    "general_config_migration_failure",
    "A general-config migration failure keeps every timer disabled.",
    world=replace(
        _BASE,
        migration_report=(
            "general_config\tgeneral\tfailed\tconfig/general.json\tinvalid legacy config",
        ),
        migration_status=16,
    ),
    tags=("error", "settings", "system"),
)
_case(
    "general_and_target_migration_failure",
    "Combined general and target migration failures report the target only once.",
    world=replace(
        _BASE,
        migration_report=(
            "general_config\tgeneral\tfailed\tconfig/general.json\tinvalid legacy config",
            "target_config\tskroutz\tfailed\tconfig/skroutz.json\tinvalid legacy config",
        ),
        migration_status=15,
    ),
    tags=("error", "target_config", "settings", "system"),
)
_case(
    "reminder_state_migration_failure",
    "A reminder-state migration failure reports storage failure after provisioning.",
    world=replace(
        _BASE,
        migration_report=(
            "reminder_state\tgeneral\tfailed\tstate/general.json\tinvalid legacy state",
        ),
        migration_status=19,
    ),
    tags=("error", "reminder", "system"),
)
_case(
    "partial_migration_recovery_retained",
    "A later failure surfaces recovery copies for an earlier successful migration.",
    world=replace(
        _BASE,
        migration_report=(
            "general_config\tgeneral\tmigrated\tconfig/general.json\tv1 to v2",
            "reminder_state\tgeneral\tfailed\tstate/general.json\tinvalid legacy state",
            "recovery\tgeneral\tretained\t/project/state/.migration-recovery.example\t",
        ),
        migration_status=19,
    ),
    tags=("error", "reminder", "system"),
)
_case(
    "migration_infrastructure_failure",
    "An unexpected migration status aborts update provisioning.",
    world=replace(_BASE, migration_status=1),
    tags=("error", "system"),
)
_case(
    "timer_only_repair",
    "A timer-only damaged installation gets its service half back.",
    world=replace(_BASE, installed_services=()),
)
_case(
    "service_only_repair",
    "A service-only damaged installation gets a newly enabled timer.",
    world=replace(
        _BASE,
        installed_timers=(),
        enabled_timers=(),
        active_timers=(),
    ),
)
_case(
    "disabled_timer_preserved",
    "A previously disabled timer remains disabled.",
    world=replace(_BASE, enabled_timers=(), active_timers=()),
)
_case(
    "new_scrapers_available",
    "A newly available scraper is reported but not installed.",
    world=replace(_BASE, plugins=("skroutz", "amazon")),
)
