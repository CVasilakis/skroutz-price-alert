"""SH_INSTALL scenarios: every user-facing transcript install.sh can produce.

install.sh acts on the *catalog* (it provisions code), so most worlds vary the venv
state, the failure injections, and the config artifacts rather than installed units.
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import DISCOVERY_ERROR, ShellWorld, shell_case

_case = shell_case(Surface.SH_INSTALL, "scripts/install.sh")

#: Everything already configured, so the transcript has no trailing config notes.
_CONFIGURED = ShellWorld(config_files=("skroutz.json", "general.json"))

_case(
    "help",
    "Usage text with debug documentation and one flag row per registered target.",
    "--help",
    world=replace(ShellWorld(), plugins=("skroutz", "amazon")),
    tags=("help",),
)

_case(
    "debug_success",
    "Debug mode exposes package-command output without changing a successful install.",
    "--debug",
    world=replace(
        _CONFIGURED,
        pip_stdout="injected pip stdout",
        pip_stderr="injected pip stderr",
    ),
    tags=("system",),
)

_case(
    "debug_failure",
    "Debug mode exposes the same package-command noise on a handled failure.",
    "--debug",
    world=replace(
        _CONFIGURED,
        pip_fail="upgrade",
        pip_stdout="injected failing pip stdout",
        pip_stderr="injected failing pip stderr",
    ),
    tags=("system", "error"),
)

_case(
    "invalid_argument",
    "A positional argument is rejected before anything runs.",
    "foo",
    tags=("error",),
)

_case(
    "python3_missing",
    "Prerequisite check: no python3 on PATH.",
    world=ShellWorld(tools="no-python3"),
    tags=("error",),
)

_case(
    "python39_rejected",
    "Python older than the supported 3.10 minimum is rejected before setup.",
    world=ShellWorld(python_version="3.9.18", python_supported=False),
    tags=("error",),
)

_case(
    "existing_venv_python39_rejected",
    "A supported system Python cannot hide an existing Python 3.9 venv.",
    world=ShellWorld(
        python_version="3.12.0",
        python_supported=True,
        venv_python_version="3.9.18",
        venv_python_supported=False,
    ),
    tags=("error",),
)

_case(
    "venv_module_missing",
    "Prerequisite check: python3 exists but the venv module is unavailable.",
    world=ShellWorld(ensurepip_missing=True),
    tags=("error",),
)

_case(
    "systemctl_missing",
    "Prerequisite check: systemd is not available.",
    world=ShellWorld(tools="no-systemctl"),
    tags=("error",),
)

_case(
    "venv_create_fails",
    "python3 -m venv fails while creating a fresh environment.",
    world=ShellWorld(venv=False, venv_create_fails=True),
    tags=("error",),
)

_case(
    "pip_upgrade_fails",
    "The pip self-upgrade inside the existing venv fails.",
    world=ShellWorld(pip_fail="upgrade"),
    tags=("error",),
)

_case(
    "requirements_missing",
    "The root requirements.txt is gone.",
    world=ShellWorld(requirements_txt=False),
    tags=("error",),
)

_case(
    "requirements_install_fails",
    "Installing the root requirements.txt fails.",
    world=ShellWorld(pip_fail="requirements"),
    tags=("error",),
)

_case(
    "discovery_failed",
    "The venv is fine but plugin discovery raises - the diagnose branch.",
    world=ShellWorld(plugins=(), discovery_error=DISCOVERY_ERROR),
    tags=("error", "catalog"),
)

_case(
    "unknown_target",
    "An explicit --<target> that is not a registered scraper.",
    "--ghost",
    tags=("error",),
)

_case(
    "bare_double_dash",
    "A bare '--' is rejected instead of silently selecting nothing.",
    "--",
    tags=("error",),
)

_case(
    "duplicate_target",
    "A repeated --<target> is de-duplicated: provisioned once.",
    "--skroutz",
    "--skroutz",
    world=replace(
        _CONFIGURED, requirements={"skroutz": "/opt/fake/scrapers/skroutz/requirements.txt"}
    ),
)

_case(
    "plugin_requirements_fail",
    "A plugin's own requirements.txt fails to install.",
    world=replace(
        _CONFIGURED,
        requirements={"skroutz": "/opt/fake/scrapers/skroutz/requirements.txt"},
        pip_fail="plugin",
    ),
    tags=("error",),
)

_case(
    "dependency_check_fails",
    "The aggregate environment is rejected before any systemd unit is written.",
    world=replace(_CONFIGURED, pip_fail="check"),
    tags=("error",),
)

_case(
    "missing_schedule",
    "A target has no catalog-resolved schedule.",
    world=ShellWorld(schedules={}),
    tags=("error",),
)

_case(
    "partial_invalid_config",
    "Healthy targets are provisioned while a malformed target remains untouched.",
    world=ShellWorld(
        plugins=("skroutz", "insomnia"),
        schedule_errors={"insomnia": "Remove unsupported keys from `config/insomnia.json`."},
        config_files=("skroutz.json", "insomnia.json", "general.json"),
    ),
    tags=("error", "target_config"),
)

_case(
    "selected_invalid_config",
    "A selected malformed target performs no unit transaction and exits with config failure.",
    "--insomnia",
    world=ShellWorld(
        plugins=("skroutz", "insomnia"),
        schedule_errors={"insomnia": "Remove unsupported keys from `config/insomnia.json`."},
        config_files=("skroutz.json", "insomnia.json", "general.json"),
    ),
    tags=("error", "target_config"),
)

_case(
    "invalid_interval",
    "An unsupported execution_interval is warned about and provisioned at the default cadence.",
    world=replace(
        _CONFIGURED,
        interval_status={"skroutz": "invalid"},
        installed_timers=("skroutz",),
        installed_services=("skroutz",),
        installed_blocks={"skroutz": "OnCalendar=*-*-* 06:00:00"},
    ),
    tags=("target_config",),
)

_case(
    "no_target_config",
    "A target with no config file still gets a timer at its canonical default.",
    world=ShellWorld(
        interval_status={"skroutz": "nocfg"},
        config_files=("general.json",),
    ),
    tags=("target_config",),
)

_case(
    "unit_write_fails",
    "The systemd user dir is unwritable, so unit rendering fails.",
    world=ShellWorld(unit_dir_readonly=True),
    tags=("error",),
)

_case(
    "enable_fails",
    "systemctl enable --now fails after the units are written.",
    world=ShellWorld(systemctl_fail=("enable",)),
    tags=("error",),
)

_case(
    "enable_noop_detected",
    "A successful enable without the required final state fails.",
    world=ShellWorld(systemctl_noop=("enable",)),
    tags=("error",),
)

_case(
    "linger_warning",
    "Lingering is off and enable-linger fails - non-fatal warning.",
    world=replace(_CONFIGURED, linger="no", linger_enable_fails=True),
)

_case(
    "fresh_install_config_notes",
    "First-ever run: venv created, lingering enabled, and pasteable config commands.",
    world=ShellWorld(venv=False, linger="no", config_dir=False),
)

# Not redundant with fresh_install_config_notes above: one missing config renders
# identically whether install.sh accumulates them as a stream or as a space-joined
# string, so only a second target pins the newline delimiting.
_case(
    "multiple_missing_configs",
    "Two unconfigured targets each get their own warning and copy command.",
    world=ShellWorld(plugins=("skroutz", "amazon"), config_dir=False),
)

_case(
    "reinstall_all_configured",
    "Re-run on a fully configured install: quiet happy path.",
    world=_CONFIGURED,
)

_case(
    "selected_install",
    "A single-target install (--skroutz) with the plugin's own requirements.",
    "--skroutz",
    world=replace(
        _CONFIGURED, requirements={"skroutz": "/opt/fake/scrapers/skroutz/requirements.txt"}
    ),
)
