import os
from pathlib import Path
from unittest import mock

import pytest

from core.scrapers.tooling.scaffold import storage
from core.scrapers.tooling.scaffold.api import create_plugin
from core.scrapers.tooling.scaffold.contracts import ScaffoldRequest
from core.scrapers.tooling.scaffold.generation import GeneratedFile, ScaffoldFiles
from core.scrapers.tooling.scaffold.storage import ScaffoldRollbackError

REQUEST = ScaffoldRequest("acme_store", "Acme Store", ("store.example",), "/products/")


def _layout(root: Path) -> None:
    (root / "src/core/scrapers/plugins").mkdir(parents=True)
    (root / "tests/plugins").mkdir(parents=True)


def test_scaffold_requires_existing_real_parent_directories(tmp_path):
    with pytest.raises(OSError, match="parent directory is missing"):
        create_plugin(tmp_path, REQUEST)

    assert not (tmp_path / "src").exists()


def test_scaffold_rejects_symlinked_parent_component(tmp_path):
    real_source = tmp_path / "real-source/core/scrapers/plugins"
    real_source.mkdir(parents=True)
    (tmp_path / "src").symlink_to(tmp_path / "real-source", target_is_directory=True)
    (tmp_path / "tests/plugins").mkdir(parents=True)

    with pytest.raises(OSError, match="must be a real directory"):
        create_plugin(tmp_path, REQUEST)

    assert not (real_source / "acme_store").exists()


def test_scaffold_removes_empty_directory_when_safe_open_fails(tmp_path):
    _layout(tmp_path)
    real_open = storage.os.open

    def fail_new_directory_open(path, flags, *args, **kwargs):
        if path == "acme_store" and kwargs.get("dir_fd") is not None:
            raise OSError("injected directory-open failure")
        return real_open(path, flags, *args, **kwargs)

    with mock.patch.object(storage.os, "open", side_effect=fail_new_directory_open):
        with pytest.raises(OSError, match="directory-open failure"):
            create_plugin(tmp_path, REQUEST)

    assert not (tmp_path / "src/core/scrapers/plugins/acme_store").exists()


def test_scaffold_never_removes_unexpected_rollback_entries(tmp_path):
    _layout(tmp_path)
    source = tmp_path / "src/core/scrapers/plugins/acme_store"

    def add_unowned_entry_then_fail(tree, _files) -> None:
        (tree.path / "unowned.txt").write_text("keep", encoding="utf-8")
        raise OSError("injected write failure")

    with mock.patch.object(storage, "_write_files", side_effect=add_unowned_entry_then_fail):
        with pytest.raises(ScaffoldRollbackError, match="unexpected entries remain") as raised:
            create_plugin(tmp_path, REQUEST)

    assert str(source) in str(raised.value)
    assert (source / "unowned.txt").read_text(encoding="utf-8") == "keep"


def test_scaffold_rejects_generated_paths_outside_the_owned_leaf(tmp_path):
    _layout(tmp_path)
    files = ScaffoldFiles((GeneratedFile("../escape.py", "unsafe"),), None)

    with mock.patch.object(storage, "render_scaffold", return_value=files):
        with pytest.raises(ValueError, match="one safe filename"):
            create_plugin(tmp_path, REQUEST)

    assert not (tmp_path / "src/core/scrapers/plugins/escape.py").exists()
    assert not (tmp_path / "src/core/scrapers/plugins/acme_store").exists()


def test_scaffold_writes_files_exclusively_relative_to_owned_directory(tmp_path):
    _layout(tmp_path)
    calls: list[tuple[object, int | None]] = []
    real_open = os.open

    def recording_open(path, flags, *args, **kwargs):
        calls.append((path, kwargs.get("dir_fd")))
        return real_open(path, flags, *args, **kwargs)

    with mock.patch.object(storage.os, "open", side_effect=recording_open):
        result = create_plugin(tmp_path, REQUEST)

    generated_names = {path.name for path in result.source.iterdir()}
    relative_file_calls = {
        path for path, directory in calls if isinstance(path, str) and directory is not None
    }
    assert generated_names <= relative_file_calls
