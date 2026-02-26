from __future__ import annotations

from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.files import delete_file, list_files, mkdir, move_file, stream_file, upload_file


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    init_git_repo(p)
    create_entity(p, "p_sec", {"name": "Sec"})
    return p


def test_files_mkdir_rejects_absolute_path(repo: Path):
    with pytest.raises(ValueError):
        mkdir(repo, "p_sec", path="/tmp/evil")


def test_files_upload_rejects_traversal(repo: Path):
    with pytest.raises(ValueError):
        upload_file(repo, "p_sec", path="../evil.txt", file_bytes=b"x", overwrite=True)


def test_files_list_rejects_traversal(repo: Path):
    with pytest.raises(ValueError):
        list_files(repo, "p_sec", path="../../")


def test_files_delete_rejects_traversal(repo: Path):
    with pytest.raises(ValueError):
        delete_file(repo, "p_sec", path="../evil.txt")


def test_files_move_rejects_traversal(repo: Path):
    with pytest.raises(ValueError):
        move_file(repo, "p_sec", src="../evil.txt", dst="ok.txt", overwrite=True)


def test_files_stream_rejects_traversal(repo: Path):
    with pytest.raises(ValueError):
        stream_file(repo, "p_sec", path="../evil.txt")


def test_files_reject_symlink_escape(repo: Path, tmp_path: Path):
    files_root = repo / "entities" / "p_sec" / "files"
    files_root.mkdir(parents=True, exist_ok=True)

    outside = tmp_path / "outside"
    outside.mkdir(parents=True, exist_ok=True)

    link = files_root / "escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except Exception:
        pytest.skip("symlinks not supported on this filesystem")

    with pytest.raises(ValueError):
        upload_file(repo, "p_sec", path="escape/evil.txt", file_bytes=b"x", overwrite=True)

    with pytest.raises(ValueError):
        list_files(repo, "p_sec", path="escape")
