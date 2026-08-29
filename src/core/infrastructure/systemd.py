"""Read-only inspection of installed systemd user units and the host's linger state."""

import getpass
import glob
import os
import subprocess

SYSTEMCTL_QUERY_TIMEOUT_SECONDS = 10
_SCRAPER_UNIT_INFIX = "-scraper."


def scraper_unit_name(target: str, suffix: str) -> str:
    """Return the conventional systemd unit name for one scraper target."""
    return f"{target}{_SCRAPER_UNIT_INFIX}{suffix}"


def get_systemd_user_dir() -> str:
    """Return the systemd user unit directory, honoring ``XDG_CONFIG_HOME``."""
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "systemd", "user")


def get_installed_plugin_units() -> dict[str, set[str]]:
    """Map installed scraper targets to their timer/service unit suffixes."""
    unit_dir = get_systemd_user_dir()
    found: dict[str, set[str]] = {}
    for suffix in ("timer", "service"):
        marker = f"{_SCRAPER_UNIT_INFIX}{suffix}"
        for path in glob.glob(os.path.join(unit_dir, f"*{marker}")):
            name = os.path.basename(path)[: -len(marker)]
            found.setdefault(name, set()).add(suffix)
    return found


def read_timer_oncalendar(target: str) -> str:
    """Return the first installed ``OnCalendar`` value, or an empty string."""
    timer_path = os.path.join(get_systemd_user_dir(), scraper_unit_name(target, "timer"))
    try:
        with open(timer_path) as timer_file:
            for line in timer_file:
                stripped = line.strip()
                if stripped.startswith("OnCalendar="):
                    return stripped[len("OnCalendar=") :].strip()
    except OSError:
        return ""
    return ""


def get_systemd_properties(unit: str, properties: str) -> dict[str, str]:
    """Query selected properties for one installed systemd user unit."""
    service_file_path = os.path.join(get_systemd_user_dir(), unit)
    if not os.path.exists(service_file_path) or os.path.getsize(service_file_path) == 0:
        return {}
    try:
        output = (
            subprocess.check_output(
                ["systemctl", "--user", "show", unit, f"--property={properties}"],
                stderr=subprocess.DEVNULL,
                timeout=SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
            )
            .decode("utf-8")
            .strip()
        )
        if not output:
            return {}
        return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    except (subprocess.SubprocessError, OSError, ValueError):
        # OSError also covers a missing or unexecutable systemctl, which is reachable
        # when unit files outlive the systemd that installed them: inspection stays
        # best-effort rather than aborting the caller's report.
        return {}


def inspect_user_lingering() -> bool | None:
    """Return whether systemd user lingering is enabled for the invoking user.

    Lingering is what lets the per-plugin timers keep firing while the user is logged
    out; ``install.sh`` enables it best-effort, and nothing re-checks it afterwards.

    ``None`` means the question could not be answered — no ``loginctl``, no running
    logind, or an unparsable answer — and is deliberately distinct from ``False``: on a
    host that has no concept of user lingering there is nothing to report, so callers
    must not read a missing answer as "disabled".
    """
    try:
        user = getpass.getuser()
        output = (
            subprocess.check_output(
                ["loginctl", "show-user", user, "--property=Linger"],
                stderr=subprocess.DEVNULL,
                timeout=SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
            )
            .decode("utf-8")
            .strip()
        )
    except (subprocess.SubprocessError, OSError, ValueError, KeyError):
        # As in get_systemd_properties, every host-side failure degrades to "unknown"
        # rather than aborting the caller's report. KeyError covers getpass.getuser()
        # on an environment with neither the usual variables nor a passwd entry.
        return None
    if output == "Linger=yes":
        return True
    if output == "Linger=no":
        return False
    return None


__all__ = [
    "SYSTEMCTL_QUERY_TIMEOUT_SECONDS",
    "get_installed_plugin_units",
    "get_systemd_properties",
    "get_systemd_user_dir",
    "inspect_user_lingering",
    "read_timer_oncalendar",
    "scraper_unit_name",
]
