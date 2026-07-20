"""SH_INSTALL scenarios: every user-facing transcript install.sh can produce.

install.sh acts on the *registry* (it provisions code), so most worlds vary the venv
state, the failure injections, and the config artifacts rather than installed units.
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import DISCOVERY_ERROR, ShellWorld, shell_case

_case = shell_case(Surface.SH_INSTALL, "install.sh")

#: Everything already configured, so the transcript has no trailing config notes.
_CONFIGURED = ShellWorld(config_files=("skroutz.json", "general.json"))

_case(
    "help",
    "Usage text with one flag row per registered target.",
    "--help",
    world=replace(ShellWorld(), plugins=("skroutz", "amazon")),
    tags=("help",),
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
    tags=("error", "registry"),
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
    "update_skips_removed_target",
    "--update skips a selection no longer in the registry.",
    "--update",
    "--ghost",
    "--skroutz",
    world=_CONFIGURED,
    tags=("orphan",),
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
    "A target has no registry-resolved schedule.",
    world=ShellWorld(schedules={}),
    tags=("error",),
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
    "First-ever run: venv created, lingering enabled, config notes.",
    world=ShellWorld(venv=False, linger="no"),
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
