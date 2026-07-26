import errno
import json
import os
from pathlib import Path
from unittest import mock

import pytest

from core.exceptions import ConfigFileError, StateFileError, StorageFileError
from core.infrastructure.persistence import (
    AtomicReplacementError,
    read_json_object,
    save_failure_message,
    storage_diagnostic,
    write_json_atomically,
)
from core.scrapers.framework.state import JsonStateRepository


def test_storage_error_exposes_separate_display_and_diagnostic_text():
    error = StorageFileError("Short display.", "Type: OSError\nDetail: disk full")

    assert str(error) == "Short display."
    assert error.display_message == "Short display."
    assert error.diagnostic_detail == "Type: OSError\nDetail: disk full"


def test_missing_required_config_has_relative_display_and_full_diagnostic(tmp_path):
    path = tmp_path / "config" / "insomnia.json"

    with pytest.raises(ConfigFileError) as caught:
        read_json_object(path, display_path="config/insomnia.json")

    error = caught.value
    assert str(error) == ("Create missing `config/insomnia.json` from the plugin example.")
    assert error.diagnostic_detail is not None
    assert f"Path: {path.resolve()}" in error.diagnostic_detail
    assert "Exception: FileNotFoundError" in error.diagnostic_detail
    assert "Errno: 2" in error.diagnostic_detail


def test_malformed_json_reports_coordinates_only_in_concise_form(tmp_path):
    path = tmp_path / "config" / "insomnia.json"
    path.parent.mkdir()
    path.write_text('{\n  "items":,\n}', encoding="utf-8")

    with pytest.raises(ConfigFileError) as caught:
        read_json_object(path, display_path="config/insomnia.json")

    error = caught.value
    assert str(error) == ("Fix JSON in `config/insomnia.json` at line 2, column 11.")
    assert "[Errno" not in str(error)
    assert error.diagnostic_detail is not None
    assert "Exception: JSONDecodeError" in error.diagnostic_detail
    assert "JSON location: line 2, column 11" in error.diagnostic_detail
    assert str(path.resolve()) in error.diagnostic_detail


def test_invalid_utf8_and_wrong_top_level_shape_are_cause_specific(tmp_path):
    path = tmp_path / "config" / "insomnia.json"
    path.parent.mkdir()
    path.write_bytes(b'{"items": "\xff"}')

    with pytest.raises(ConfigFileError) as invalid_utf8:
        read_json_object(path, display_path="config/insomnia.json")
    assert str(invalid_utf8.value) == ("`config/insomnia.json` is not valid UTF-8.")
    assert "UnicodeDecodeError" in (invalid_utf8.value.diagnostic_detail or "")

    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ConfigFileError) as wrong_shape:
        read_json_object(path, display_path="config/insomnia.json")
    assert str(wrong_shape.value) == ("`config/insomnia.json` must contain a JSON object.")
    assert "top-level JSON value is list" in (wrong_shape.value.diagnostic_detail or "")


@pytest.mark.parametrize(
    ("failure", "display"),
    [
        (
            PermissionError(errno.EACCES, "denied"),
            "Cannot read `config/insomnia.json`; check its permissions.",
        ),
        (
            OSError(errno.EIO, "device error"),
            "Cannot read `config/insomnia.json`; check the error log.",
        ),
    ],
)
def test_read_io_failures_hide_errno_but_preserve_it_in_diagnostics(tmp_path, failure, display):
    path = tmp_path / "config" / "insomnia.json"
    with mock.patch.object(Path, "open", side_effect=failure):
        with pytest.raises(ConfigFileError) as caught:
            read_json_object(path, display_path="config/insomnia.json")

    assert str(caught.value) == display
    assert "[Errno" not in str(caught.value)
    assert caught.value.diagnostic_detail is not None
    assert f"Errno: {failure.errno}" in caught.value.diagnostic_detail
    assert str(path.resolve()) in caught.value.diagnostic_detail


def test_diagnostic_path_resolution_never_masks_symlink_loop_failures(tmp_path):
    loop = tmp_path / "loop"
    loop.symlink_to(loop.name)

    with pytest.raises(ConfigFileError) as config_failure:
        read_json_object(loop, display_path="config/loop.json")
    assert type(config_failure.value.__cause__) is OSError
    assert config_failure.value.diagnostic_detail is not None
    assert f"Path: {os.path.abspath(loop)}" in config_failure.value.diagnostic_detail

    state = JsonStateRepository(loop, display_path="state/loop.json")
    with pytest.raises(StateFileError) as state_failure:
        state.load()
    assert type(state_failure.value.__cause__) is OSError
    assert state_failure.value.diagnostic_detail is not None
    assert f"Path: {os.path.abspath(loop)}" in state_failure.value.diagnostic_detail


def test_unusual_errno_cannot_break_diagnostic_generation(tmp_path):
    failure = OSError("device error")
    failure.errno = 10**100

    diagnostic = storage_diagnostic(tmp_path / "state.json", failure, operation="read state")

    assert f"Errno: {failure.errno} (unknown error)" in diagnostic


def test_atomic_replacement_error_preserves_permission_classification():
    error = AtomicReplacementError(
        "atomic replacement failed",
        destination_replaced=False,
        error_number=errno.EACCES,
    )

    assert save_failure_message("state/example.json", error) == (
        "Cannot save `state/example.json`; check its permissions."
    )


def test_atomic_json_writer_keeps_format_and_commits_through_shared_helper(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    events = []

    def commit(destination, temporary):
        events.append((Path(destination), Path(temporary)))
        os.replace(temporary, destination)

    monkeypatch.setattr("core.infrastructure.persistence.commit_atomic_replacement", commit)

    write_json_atomically(path, {"value": 1})

    assert events == [(path, Path(f"{path}.tmp"))]
    assert path.read_text(encoding="utf-8") == '{\n  "value": 1\n}'
