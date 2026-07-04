"""Shared helpers for the test suite (importable: ``tests`` is on the pythonpath).

Currently the ``config/general.json`` read/write helpers used by both the reminder unit
tests and the general-settings integration tests, built on the production
``general_config_path`` so the filename lives in exactly one place.
"""

import json

from core.general import general_config_path


def write_general(cfg_dir, data) -> None:
    """Writes ``data`` as JSON to ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir)), "w") as f:
        json.dump(data, f)


def read_general(cfg_dir):
    """Reads and returns the parsed ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir))) as f:
        return json.load(f)
