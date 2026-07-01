"""Configuration Check panel scenarios (shared by ``--status`` and the interactive run).

``drive_config`` patches the three external seams (update check, env check, URL
classification) and calls the real ``config_check._append_*`` helpers, so the version row,
the per-target config rows, and the .env row render with production logic from synthetic
``TargetLoad`` outcomes and counts.
"""

from ui.catalog._base import scenario, Surface
from ui.catalog.inputs import target_load
from ui.harness.drivers import drive_config

# Faithful storage/env messages (see scrapers/base/storage.py and utils.py).
MISSING = "The config/skroutz.json file is missing or not a file"
PERMS = "The config/skroutz.json file has wrong permissions"
BAD_JSON = "The config/skroutz.json file contains invalid JSON format"
ENV_NONE = "No .env file found or unreadable"


@scenario(Surface.CONFIG, "all_good", "Up to date, items loaded, valid .env", tags=("ok",))
def _():
    return drive_config("uptodate", [target_load("skroutz", 5)], valid_count=2)


@scenario(Surface.CONFIG, "update_available", "A newer version is available", tags=("version",))
def _():
    return drive_config("available", [target_load("skroutz", 5)], valid_count=2)


@scenario(Surface.CONFIG, "update_check_error", "The update check could not reach the remote", tags=("version",))
def _():
    return drive_config("error", [target_load("skroutz", 5)], valid_count=2)


@scenario(Surface.CONFIG, "faulty_items", "Some products are misconfigured", tags=("config",))
def _():
    return drive_config("uptodate", [target_load("skroutz", 8, faulty_indices=[2, 5])], valid_count=2)


@scenario(Surface.CONFIG, "faulty_items_long", "Many misconfigured products (footnote wraps)", tags=("config", "layout"))
def _():
    indices = [1, 2, 3, 5, 8, 11, 13, 16, 18, 21, 24, 27]
    return drive_config("uptodate", [target_load("skroutz", 30, faulty_indices=indices)], valid_count=2)


@scenario(Surface.CONFIG, "load_missing", "Config file missing", tags=("config", "error"))
def _():
    return drive_config("uptodate", [target_load("skroutz", 0, error=MISSING)], valid_count=2)


@scenario(Surface.CONFIG, "load_permissions", "Config file has wrong permissions", tags=("config", "error"))
def _():
    return drive_config("uptodate", [target_load("skroutz", 0, error=PERMS)], valid_count=2)


@scenario(Surface.CONFIG, "load_invalid_json", "Config file is invalid JSON", tags=("config", "error"))
def _():
    return drive_config("uptodate", [target_load("skroutz", 0, error=BAD_JSON)], valid_count=2)


@scenario(Surface.CONFIG, "env_mixed", "Some notification URLs are invalid", tags=("env",))
def _():
    return drive_config("uptodate", [target_load("skroutz", 5)], valid_count=1, invalid_count=2)


@scenario(Surface.CONFIG, "env_not_configured", "No usable notification URLs", tags=("env", "error"))
def _():
    return drive_config("uptodate", [target_load("skroutz", 5)], valid_count=0, invalid_count=0, env_error=ENV_NONE)


@scenario(Surface.CONFIG, "multi_target", "Two targets: one clean, one with faulty items", tags=("config",))
def _():
    return drive_config(
        "uptodate",
        [target_load("skroutz", 5), target_load("amazon", 4, faulty_indices=[3])],
        valid_count=2,
    )


@scenario(Surface.CONFIG, "worst_case", "Update error + load failure + no .env (all issues)", tags=("combined", "error"))
def _():
    return drive_config("error", [target_load("skroutz", 0, error=BAD_JSON)], valid_count=0, invalid_count=0, env_error=ENV_NONE)
