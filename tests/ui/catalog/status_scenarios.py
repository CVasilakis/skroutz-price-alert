"""``--status`` panel scenarios: per-plugin Service Status, not-installed, and orphans.

Each Service Status scenario feeds ``status.build_service_panel`` synthetic systemd
property dicts + a ``ResolvedSettings``, exercising the real settings section, the
timer/last-execution/next-execution rows, the exit-code verdict table, and the
schedule-drift footnote.
"""

from ui.catalog._base import scenario, Surface
from ui.catalog.inputs import (
    resolved_settings, malformed_block_warning, timer_props, service_props,
    config_faulty, config_failed, STORAGE_BAD_JSON,
)
from ui.harness.drivers import drive_service, drive_not_installed, drive_orphan
from core.scrapers.base.settings import STATUS_OK, STATUS_DEFAULT, STATUS_INVALID, STATUS_NOCFG

TARGET = "skroutz"
CFG = "skroutz.json"
RAN_AT = "Sun 2026-06-28 13:00:00 UTC"
NEXT_AT = "Mon 2026-06-29 13:00:00 UTC"


def _svc(result="success", code="0", running=False, exec_start=RAN_AT):
    return service_props(running=running, result=result, exec_status=code, exec_start=exec_start)


# --- Settings section variants -------------------------------------------------------

@scenario(Surface.STATUS, "service_healthy", "Installed, all settings valid, timer active, last run OK", tags=("ok",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved_settings(),
                         CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_defaults", "All settings unset (active defaults shown)", tags=("settings",))
def _():
    resolved = resolved_settings(
        interval=("1h", STATUS_DEFAULT, None),
        retention=(7, STATUS_DEFAULT, None),
        notify=(True, STATUS_DEFAULT, None),
    )
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_nocfg", "Config file missing (settings fall back to defaults)", tags=("settings",))
def _():
    resolved = resolved_settings(
        interval=("1h", STATUS_NOCFG, None),
        retention=(7, STATUS_NOCFG, None),
        notify=(True, STATUS_NOCFG, None),
    )
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_invalid_interval", "An unsupported execution_interval", tags=("settings",))
def _():
    resolved = resolved_settings(interval=("1h", STATUS_INVALID, "3h"))
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_invalid_retention", "An out-of-range log_retention_days", tags=("settings",))
def _():
    resolved = resolved_settings(retention=(7, STATUS_INVALID, 99))
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_invalid_notify", "An unrecognized notify_scraping_errors value", tags=("settings",))
def _():
    resolved = resolved_settings(notify=(True, STATUS_INVALID, "maybe"))
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "service_block_ignored", "A malformed settings block (ignored)", tags=("settings",))
def _():
    resolved = resolved_settings(
        interval=("1h", STATUS_DEFAULT, None),
        retention=(7, STATUS_DEFAULT, None),
        notify=(True, STATUS_DEFAULT, None),
        block_warning=malformed_block_warning(),
    )
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "hourly")


# --- Products-config ('Config' row) variants -----------------------------------------
# The healthy 'Config' row is exercised by every scenario above (drive_service defaults to
# a clean load); these cover the faulty / failed / unavailable variants.

@scenario(Surface.STATUS, "config_faulty", "Some products are misconfigured (Config row)", tags=("products",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved_settings(),
                         CFG, "hourly", "hourly", config=config_faulty())


@scenario(Surface.STATUS, "config_failed", "Products config failed to load (Config row)", tags=("products", "error"))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved_settings(),
                         CFG, "hourly", "hourly", config=config_failed(STORAGE_BAD_JSON))


@scenario(Surface.STATUS, "config_unavailable", "Dependencies missing (no Config row)", tags=("products", "system"))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved_settings(),
                         CFG, "hourly", "hourly", config=None)


# --- Timer / last-execution / next-execution variants --------------------------------

@scenario(Surface.STATUS, "timer_inactive", "The systemd timer is not active", tags=("timer",))
def _():
    return drive_service(TARGET, timer_props(False, NEXT_AT), _svc(), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_skipped", "Last run skipped (another instance was running)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "42"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_products_error", "Last run failed on the products config (exit 15)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "15"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_env_error", "Last run failed on the .env (exit 16)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "16"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_rate_limit", "Last run was rate limited (exit 17)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "17"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_scrape_error", "Last run exhausted a parser or unexpected scraper fault (exit 18)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "18"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_storage_error", "Last run could not persist scrape state (exit 19)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "19"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_notification_error", "Last run missed at least one notification (exit 20)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "20"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_dependency_error", "Last run lacked scraper dependencies (exit 21)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "21"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_interrupt", "Last run was interrupted (exit 130)", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("exit-code", "130"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "exec_unknown_code", "Last run failed with an unrecognized exit code", tags=("last_run",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc("signal", "9"), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "running_now", "The service is currently running", tags=("in_progress",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(running=True), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "not_scheduled", "Installed but the timer has no next elapse", tags=("timer",))
def _():
    return drive_service(TARGET, timer_props(True, "0"), _svc(), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "never_run", "Installed and scheduled but never executed yet", tags=("ok",))
def _():
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(exec_start=""), resolved_settings(), CFG, "hourly", "hourly")


@scenario(Surface.STATUS, "schedule_drift", "Live timer differs from the configured interval", tags=("timer",))
def _():
    # interval ok/default, but the on-disk OnCalendar ("*:0/30") != the configured one.
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved_settings(), CFG, "hourly", "*:0/30")


@scenario(Surface.STATUS, "drift_suppressed_invalid_interval", "An invalid interval suppresses the drift footnote", tags=("timer", "settings"))
def _():
    # The on-disk OnCalendar differs, but the configured interval is *invalid* — the
    # Execution Interval row owns that problem, so the panel's own gate must NOT add
    # the drift footnote on top (build_service_panel checks the interval status even
    # when a caller hands it differing schedules).
    resolved = resolved_settings(interval=("1h", STATUS_INVALID, "3h"))
    return drive_service(TARGET, timer_props(True, NEXT_AT), _svc(), resolved, CFG, "hourly", "*:0/30")


@scenario(Surface.STATUS, "service_many_issues", "Invalid setting + timer down + failed run + drift", tags=("combined",))
def _():
    resolved = resolved_settings(
        interval=("1h", STATUS_OK, "1h"),
        retention=(7, STATUS_INVALID, 99),
    )
    return drive_service(TARGET, timer_props(False, NEXT_AT), _svc("exit-code", "15"), resolved, CFG, "hourly", "*:0/30")


# --- Whole panels (not built via the per-row settings/systemd path) ------------------

@scenario(Surface.STATUS, "not_installed", "Plugin registered but no units installed", tags=("error",))
def _():
    return drive_not_installed(TARGET)


@scenario(Surface.STATUS, "orphan", "Units installed for a plugin no longer registered", tags=("orphan",))
def _():
    return drive_orphan("oldstore")
