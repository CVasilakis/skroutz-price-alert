"""Configuration Check panel scenarios (shared by ``--status`` and the interactive run).

``drive_config`` patches the three external seams (update check, env check, URL
classification) and calls the real ``config_check._append_*`` helpers, so the version row
and the .env row render with production logic. Per-scraper products-config health is no
longer on this panel — it now leads each Service Status panel (STATUS surface) and each
Scraping panel (RUN surface).
"""

from ui.catalog._base import scenario, Surface
from ui.catalog.inputs import ENV_NONE
from ui.harness.drivers import drive_config


@scenario(Surface.CONFIG, "all_good", "Up to date, valid .env", tags=("ok",))
def _():
    return drive_config("uptodate", valid_count=2)


@scenario(Surface.CONFIG, "update_available", "A newer version is available", tags=("version",))
def _():
    return drive_config("available", valid_count=2)


@scenario(Surface.CONFIG, "update_check_error", "The update check could not reach the remote", tags=("version",))
def _():
    return drive_config("error", valid_count=2)


@scenario(Surface.CONFIG, "env_mixed", "Some notification URLs are invalid", tags=("env",))
def _():
    return drive_config("uptodate", valid_count=1, invalid_count=2)


@scenario(Surface.CONFIG, "env_not_configured", "No usable notification URLs", tags=("env", "error"))
def _():
    return drive_config("uptodate", valid_count=0, invalid_count=0, env_error=ENV_NONE)


@scenario(Surface.CONFIG, "worst_case", "Update error + no .env (all global issues)", tags=("combined", "error"))
def _():
    return drive_config("error", valid_count=0, invalid_count=0, env_error=ENV_NONE)
