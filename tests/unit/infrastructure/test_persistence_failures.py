import errno
import json
from pathlib import Path
from unittest import mock

import pytest

from core.exceptions import ConfigFileError, StorageFileError
from core.infrastructure.persistence import read_json_object


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
    assert str(error) == (
        "Create missing `config/insomnia.json` from the plugin example."
    )
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
    assert str(error) == (
        "Fix JSON in `config/insomnia.json` at line 2, column 11."
    )
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
    assert str(invalid_utf8.value) == (
        "`config/insomnia.json` is not valid UTF-8."
    )
    assert "UnicodeDecodeError" in (invalid_utf8.value.diagnostic_detail or "")

    path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
    with pytest.raises(ConfigFileError) as wrong_shape:
        read_json_object(path, display_path="config/insomnia.json")
    assert str(wrong_shape.value) == (
        "`config/insomnia.json` must contain a JSON object."
    )
    assert "top-level JSON value is list" in (
        wrong_shape.value.diagnostic_detail or ""
    )


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
def test_read_io_failures_hide_errno_but_preserve_it_in_diagnostics(
    tmp_path, failure, display
):
    path = tmp_path / "config" / "insomnia.json"
    with mock.patch.object(Path, "open", side_effect=failure):
        with pytest.raises(ConfigFileError) as caught:
            read_json_object(path, display_path="config/insomnia.json")

    assert str(caught.value) == display
    assert "[Errno" not in str(caught.value)
    assert caught.value.diagnostic_detail is not None
    assert f"Errno: {failure.errno}" in caught.value.diagnostic_detail
    assert str(path.resolve()) in caught.value.diagnostic_detail

