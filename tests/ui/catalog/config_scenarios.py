"""Configuration Check panel scenarios (shared by ``--status`` and the interactive run).

``drive_config`` feeds an immutable general-config outcome to the real
``config_check._append_*`` helpers. Per-scraper products-config health is shown on each
Service Status and Scraping panel instead.
"""

from ui.catalog._base import Surface, scenario
from ui.catalog.inputs import NOTIFICATIONS_NONE
from ui.harness.drivers import drive_config


@scenario(Surface.CONFIG, "all_good", "Up to date, valid notifications", tags=("ok",))
def _():
    return drive_config("uptodate", valid_count=2)


@scenario(Surface.CONFIG, "update_available", "A newer version is available")
def _():
    return drive_config("available", valid_count=2)


@scenario(
    Surface.CONFIG,
    "update_check_error",
    "The update check could not reach the remote",
    tags=("error",),
)
def _():
    return drive_config("error", valid_count=2)


@scenario(
    Surface.CONFIG, "reminder_set", "An explicitly configured reminder cadence", tags=("reminder",)
)
def _():
    return drive_config("uptodate", valid_count=2, reminder_raw="1 week")


@scenario(
    Surface.CONFIG,
    "reminder_invalid",
    "An unsupported reminder value falls back to the default",
    tags=("reminder",),
)
def _():
    return drive_config("uptodate", valid_count=2, reminder_raw="fortnightly")


@scenario(
    Surface.CONFIG,
    "reminder_schedule_set",
    "A customized reminder day and time",
    tags=("reminder",),
)
def _():
    return drive_config(
        "uptodate",
        valid_count=2,
        reminder_raw="1 month",
        reminder_day_raw="Monday",
        reminder_time_raw="9:00",
    )


@scenario(
    Surface.CONFIG,
    "reminder_schedule_invalid",
    "Unsupported reminder day/time fall back to defaults",
    tags=("reminder",),
)
def _():
    return drive_config(
        "uptodate", valid_count=2, reminder_day_raw="Funday", reminder_time_raw="25:00"
    )


@scenario(
    Surface.CONFIG,
    "notifications_mixed",
    "Some notification URLs are invalid",
    tags=("error",),
)
def _():
    return drive_config("uptodate", valid_count=1, invalid_count=2)


@scenario(
    Surface.CONFIG,
    "notifications_not_configured",
    "No usable notification URLs",
    tags=("error",),
)
def _():
    return drive_config("uptodate", valid_count=0, invalid_count=0, config_error=NOTIFICATIONS_NONE)


@scenario(
    Surface.CONFIG,
    "unsafe_permissions",
    "Valid notifications in a group-readable general config",
    tags=("error",),
)
def _():
    return drive_config(
        "uptodate",
        valid_count=2,
        permission_warning="Protect notification URLs: `chmod 600 config/general.json`.",
    )


@scenario(
    Surface.CONFIG,
    "settings_failed_notifications_healthy",
    "Reminder settings fail without disabling notifications",
    tags=("combined", "error"),
)
def _():
    return drive_config(
        "uptodate",
        valid_count=2,
        settings_error="Remove unsupported settings from `config/general.json`.",
    )


@scenario(
    Surface.CONFIG,
    "malformed_general",
    "Malformed general config fails both sections without duplicate notes",
    tags=("combined", "error"),
)
def _():
    error = "Fix JSON in `config/general.json` at line 7, column 3."
    return drive_config(
        "uptodate",
        config_error=error,
        settings_error=error,
    )


@scenario(
    Surface.CONFIG,
    "worst_case",
    "Update error + no notification configuration",
    tags=("combined", "error"),
)
def _():
    return drive_config("error", valid_count=0, invalid_count=0, config_error=NOTIFICATIONS_NONE)
