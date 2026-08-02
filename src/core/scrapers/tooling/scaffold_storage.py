"""Fail-closed additive storage for generated plugin scaffolds."""

from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePath

from core.scrapers.tooling.scaffold_contracts import ScaffoldRequest, ScaffoldResult
from core.scrapers.tooling.scaffold_generation import GeneratedFile, render_scaffold


@dataclass(frozen=True)
class ScaffoldDestinations:
    source: Path
    tests: Path


@dataclass
class _CreatedTree:
    parent: Path
    parent_descriptor: int
    name: str
    path: Path
    descriptor: int | None = None
    device: int | None = None
    inode: int | None = None
    files: list[str] = field(default_factory=list)


class ScaffoldRollbackError(RuntimeError):
    """A scaffold failed and one or more new paths could not be removed."""


def scaffold_destinations(repo_root: Path, target: str) -> ScaffoldDestinations:
    root = repo_root.resolve()
    return ScaffoldDestinations(
        source=root / "src" / "core" / "scrapers" / "plugins" / target,
        tests=root / "tests" / "plugins" / target,
    )


def path_entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def scaffold_collisions(repo_root: Path, target: str) -> tuple[Path, ...]:
    destinations = scaffold_destinations(repo_root, target)
    return tuple(
        path for path in (destinations.source, destinations.tests) if path_entry_exists(path)
    )


def _validate_parent(repo_root: Path, parent: Path) -> None:
    try:
        relative = parent.relative_to(repo_root)
    except ValueError as exc:
        raise OSError(f"scaffold parent escapes the repository root: {parent}") from exc
    current = repo_root
    for component in relative.parts:
        current /= component
        try:
            details = current.lstat()
        except FileNotFoundError as exc:
            raise OSError(f"required scaffold parent directory is missing: {current}") from exc
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise OSError(f"required scaffold parent must be a real directory: {current}")


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_parent(repo_root: Path, parent: Path) -> int:
    _validate_parent(repo_root, parent)
    try:
        return os.open(parent, _directory_flags())
    except OSError as exc:
        raise OSError(f"could not safely open scaffold parent directory {parent}: {exc}") from exc


def _begin_tree(repo_root: Path, path: Path) -> _CreatedTree:
    parent_descriptor = _open_parent(repo_root, path.parent)
    tree = _CreatedTree(path.parent, parent_descriptor, path.name, path)
    directory_created = False
    try:
        os.mkdir(tree.name, dir_fd=parent_descriptor)
        directory_created = True
        try:
            tree.descriptor = os.open(tree.name, _directory_flags(), dir_fd=parent_descriptor)
        except OSError as exc:
            raise OSError(f"could not safely open new scaffold directory {path}: {exc}") from exc
        details = os.fstat(tree.descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise OSError(f"new scaffold destination is not a directory: {path}")
        tree.device = details.st_dev
        tree.inode = details.st_ino
        return tree
    except BaseException:
        if tree.descriptor is not None:
            os.close(tree.descriptor)
            tree.descriptor = None
        if directory_created:
            try:
                os.rmdir(tree.name, dir_fd=parent_descriptor)
            except OSError as cleanup_error:
                os.close(parent_descriptor)
                raise ScaffoldRollbackError(
                    "scaffold directory setup failed and rollback was incomplete; "
                    f"recovery path: {path}: {cleanup_error}"
                ) from None
        os.close(parent_descriptor)
        raise


def _write_files(tree: _CreatedTree, files: tuple[GeneratedFile, ...]) -> None:
    assert tree.descriptor is not None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    for generated in files:
        relative = PurePath(generated.relative_path)
        if relative.name != generated.relative_path or relative.name in {"", ".", ".."}:
            raise ValueError(
                f"generated scaffold path must be one safe filename: {generated.relative_path!r}"
            )
        descriptor = os.open(relative.name, flags, 0o666, dir_fd=tree.descriptor)
        tree.files.append(relative.name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                output.write(generated.contents)
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise


def _rollback_tree(tree: _CreatedTree) -> tuple[str, ...]:
    failures: list[str] = []
    if tree.descriptor is not None:
        for filename in reversed(tree.files):
            try:
                os.unlink(filename, dir_fd=tree.descriptor)
            except FileNotFoundError:
                continue
            except OSError as exc:
                failures.append(f"{tree.path / filename}: {exc}")
        try:
            details = os.stat(tree.name, dir_fd=tree.parent_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            failures.append(f"{tree.path}: created directory was moved or removed unexpectedly")
        except OSError as exc:
            failures.append(f"{tree.path}: could not verify created directory: {exc}")
        else:
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_dev != tree.device
                or details.st_ino != tree.inode
            ):
                failures.append(f"{tree.path}: path was replaced after creation; refusing cleanup")
            else:
                try:
                    os.rmdir(tree.name, dir_fd=tree.parent_descriptor)
                except OSError as exc:
                    detail = (
                        "unexpected entries remain" if exc.errno == errno.ENOTEMPTY else str(exc)
                    )
                    failures.append(f"{tree.path}: {detail}")
        os.close(tree.descriptor)
        tree.descriptor = None
    os.close(tree.parent_descriptor)
    return tuple(failures)


def _close_tree(tree: _CreatedTree) -> None:
    if tree.descriptor is not None:
        os.close(tree.descriptor)
        tree.descriptor = None
    os.close(tree.parent_descriptor)


def create_plugin(repo_root: Path, request: ScaffoldRequest) -> ScaffoldResult:
    """Commit one validated scaffold without following or overwriting managed paths."""
    destinations = scaffold_destinations(repo_root, request.target)
    collisions = scaffold_collisions(repo_root, request.target)
    if collisions:
        joined = ", ".join(str(path) for path in collisions)
        raise FileExistsError(f"refusing to overwrite existing path(s): {joined}")

    files = render_scaffold(request)
    created: list[_CreatedTree] = []
    try:
        source_tree = _begin_tree(repo_root.resolve(), destinations.source)
        created.append(source_tree)
        _write_files(source_tree, files.source)
        tests_path: Path | None = None
        if files.tests is not None:
            tests_tree = _begin_tree(repo_root.resolve(), destinations.tests)
            created.append(tests_tree)
            _write_files(tests_tree, files.tests)
            tests_path = destinations.tests
    except BaseException as exc:
        failures: list[str] = []
        for tree in reversed(created):
            failures.extend(_rollback_tree(tree))
        if failures:
            raise ScaffoldRollbackError(
                "scaffold failed and rollback was incomplete; recovery paths: "
                + "; ".join(failures)
            ) from exc
        raise
    for tree in created:
        _close_tree(tree)
    return ScaffoldResult(destinations.source, tests_path)


__all__ = [
    "ScaffoldDestinations",
    "ScaffoldRollbackError",
    "create_plugin",
    "path_entry_exists",
    "scaffold_collisions",
    "scaffold_destinations",
]
