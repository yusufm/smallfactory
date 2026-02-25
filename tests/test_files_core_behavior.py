from __future__ import annotations

import io
import subprocess
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.files import (
    delete_file,
    list_files,
    mkdir,
    move_file,
    rmdir,
    stream_file,
    upload_file,
    zip_files,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    _init_git_repo(p)
    create_entity(p, "p_files", {"name": "Files Part"})
    return p


def test_files_round_trip_upload_list_stream_move_delete(repo: Path):
    mk = mkdir(repo, "p_files", path="docs")
    assert mk["path"] == "docs"

    up = upload_file(repo, "p_files", path="docs/readme.txt", file_bytes=b"hello", overwrite=False)
    assert up["path"] == "docs/readme.txt"
    assert up["size"] == 5

    listed = list_files(repo, "p_files", path="docs")
    items = listed.get("items") or []
    assert len(items) == 1
    assert items[0]["type"] == "file"
    assert items[0]["path"] == "docs/readme.txt"

    streamed = stream_file(repo, "p_files", path="docs/readme.txt")
    assert streamed["filename"] == "readme.txt"
    assert streamed["bytes"] == b"hello"
    assert streamed["mimetype"] == "text/plain"

    mv = move_file(repo, "p_files", src="docs/readme.txt", dst="docs/readme_v2.txt")
    assert mv["src"] == "docs/readme.txt"
    assert mv["dst"] == "docs/readme_v2.txt"

    with pytest.raises(FileNotFoundError):
        stream_file(repo, "p_files", path="docs/readme.txt")

    rm = delete_file(repo, "p_files", path="docs/readme_v2.txt")
    assert rm["removed"] == "docs/readme_v2.txt"

    listed_after = list_files(repo, "p_files", path="docs")
    assert listed_after["items"] == []

    removed_dir = rmdir(repo, "p_files", path="docs")
    assert removed_dir["removed"] == "docs"


def test_rmdir_rejects_non_empty_folder(repo: Path):
    mkdir(repo, "p_files", path="pack")
    upload_file(repo, "p_files", path="pack/a.bin", file_bytes=b"x", overwrite=False)

    with pytest.raises(OSError, match="Folder is not empty"):
        rmdir(repo, "p_files", path="pack")


def test_zip_files_includes_nested_files_and_omits_gitkeep(repo: Path):
    mkdir(repo, "p_files", path="bundle/sub")
    upload_file(repo, "p_files", path="bundle/a.txt", file_bytes=b"A", overwrite=False)
    upload_file(repo, "p_files", path="bundle/sub/b.txt", file_bytes=b"B", overwrite=False)

    zbytes = zip_files(repo, "p_files", paths=["bundle"])
    assert isinstance(zbytes, (bytes, bytearray))
    assert len(zbytes) > 0

    with zipfile.ZipFile(io.BytesIO(zbytes), mode="r") as zf:
        names = sorted(zf.namelist())
        assert "bundle/a.txt" in names
        assert "bundle/sub/b.txt" in names
        assert all(not n.endswith(".gitkeep") for n in names)
