"""Extended tests for smallfactory.core.v1.files — covers zip_files, rmdir,
move_dir, move_file edge cases, and stream_file."""
from __future__ import annotations

import zipfile
import io
from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.files import (
    delete_file,
    list_files,
    mkdir,
    move_dir,
    move_file,
    rmdir,
    stream_file,
    upload_file,
    zip_files,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    init_git_repo(p)
    create_entity(p, "p_item", {"name": "Test Item"})
    return p


# ---------------------------------------------------------------------------
# zip_files
# ---------------------------------------------------------------------------

class TestZipFiles:

    def test_zip_single_file(self, repo: Path):
        upload_file(repo, "p_item", path="readme.txt", file_bytes=b"hello world")
        data = zip_files(repo, "p_item", paths=["readme.txt"])
        zf = zipfile.ZipFile(io.BytesIO(data))
        assert "readme.txt" in zf.namelist()
        assert zf.read("readme.txt") == b"hello world"

    def test_zip_multiple_files(self, repo: Path):
        upload_file(repo, "p_item", path="a.txt", file_bytes=b"aaa")
        upload_file(repo, "p_item", path="b.txt", file_bytes=b"bbb")
        data = zip_files(repo, "p_item", paths=["a.txt", "b.txt"])
        zf = zipfile.ZipFile(io.BytesIO(data))
        assert set(zf.namelist()) == {"a.txt", "b.txt"}

    def test_zip_directory_includes_contents(self, repo: Path):
        mkdir(repo, "p_item", path="docs")
        upload_file(repo, "p_item", path="docs/spec.pdf", file_bytes=b"pdf-data")
        upload_file(repo, "p_item", path="docs/notes.txt", file_bytes=b"notes")
        data = zip_files(repo, "p_item", paths=["docs"])
        zf = zipfile.ZipFile(io.BytesIO(data))
        names = zf.namelist()
        assert "docs/spec.pdf" in names
        assert "docs/notes.txt" in names

    def test_zip_skips_gitkeep(self, repo: Path):
        mkdir(repo, "p_item", path="empty")
        data = zip_files(repo, "p_item", paths=["empty"])
        zf = zipfile.ZipFile(io.BytesIO(data))
        for name in zf.namelist():
            assert ".gitkeep" not in name

    def test_zip_nonexistent_path_skipped(self, repo: Path):
        upload_file(repo, "p_item", path="real.txt", file_bytes=b"data")
        data = zip_files(repo, "p_item", paths=["real.txt", "ghost.txt"])
        zf = zipfile.ZipFile(io.BytesIO(data))
        assert "real.txt" in zf.namelist()
        assert "ghost.txt" not in zf.namelist()

    def test_zip_empty_paths_list(self, repo: Path):
        data = zip_files(repo, "p_item", paths=[])
        zf = zipfile.ZipFile(io.BytesIO(data))
        assert zf.namelist() == []


# ---------------------------------------------------------------------------
# rmdir
# ---------------------------------------------------------------------------

class TestRmdir:

    def test_remove_empty_dir(self, repo: Path):
        mkdir(repo, "p_item", path="empty_folder")
        result = rmdir(repo, "p_item", path="empty_folder")
        assert "empty_folder" in result["removed"]
        # Verify directory is gone
        root = repo / "entities" / "p_item" / "files"
        assert not (root / "empty_folder").exists()

    def test_remove_non_empty_dir_raises(self, repo: Path):
        mkdir(repo, "p_item", path="has_stuff")
        upload_file(repo, "p_item", path="has_stuff/file.txt", file_bytes=b"data")
        with pytest.raises(OSError, match="not empty"):
            rmdir(repo, "p_item", path="has_stuff")

    def test_remove_nonexistent_dir_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError):
            rmdir(repo, "p_item", path="nope")

    def test_cannot_remove_files_root(self, repo: Path):
        with pytest.raises(ValueError, match="files root"):
            rmdir(repo, "p_item", path=".")


# ---------------------------------------------------------------------------
# move_dir
# ---------------------------------------------------------------------------

class TestMoveDir:

    def test_move_directory(self, repo: Path):
        mkdir(repo, "p_item", path="src")
        upload_file(repo, "p_item", path="src/main.c", file_bytes=b"int main(){}")
        result = move_dir(repo, "p_item", src="src", dst="source")
        assert result["src"] == "src"
        assert result["dst"] == "source"
        # New location should have the file
        items = list_files(repo, "p_item", path="source", recursive=True)
        names = [i["name"] for i in items["items"]]
        assert "main.c" in names

    def test_move_dir_source_not_found(self, repo: Path):
        with pytest.raises(FileNotFoundError, match="Source folder"):
            move_dir(repo, "p_item", src="ghost", dst="target")

    def test_move_dir_source_is_file_raises(self, repo: Path):
        upload_file(repo, "p_item", path="afile.txt", file_bytes=b"data")
        with pytest.raises(FileNotFoundError, match="Source folder"):
            move_dir(repo, "p_item", src="afile.txt", dst="target")

    def test_cannot_move_files_root(self, repo: Path):
        with pytest.raises(ValueError, match="files root"):
            move_dir(repo, "p_item", src=".", dst="elsewhere")

    def test_move_dir_dst_exists_without_overwrite(self, repo: Path):
        mkdir(repo, "p_item", path="a")
        mkdir(repo, "p_item", path="b")
        with pytest.raises(FileExistsError):
            move_dir(repo, "p_item", src="a", dst="b")

    def test_move_dir_dst_is_file_raises(self, repo: Path):
        mkdir(repo, "p_item", path="mydir")
        upload_file(repo, "p_item", path="myfile.txt", file_bytes=b"data")
        with pytest.raises(NotADirectoryError):
            move_dir(repo, "p_item", src="mydir", dst="myfile.txt")


# ---------------------------------------------------------------------------
# move_file edge cases
# ---------------------------------------------------------------------------

class TestMoveFileEdgeCases:

    def test_move_file_basic(self, repo: Path):
        upload_file(repo, "p_item", path="old.txt", file_bytes=b"content")
        result = move_file(repo, "p_item", src="old.txt", dst="new.txt")
        assert result["src"] == "old.txt"
        assert result["dst"] == "new.txt"
        # Old should be gone
        with pytest.raises(FileNotFoundError):
            stream_file(repo, "p_item", path="old.txt")
        # New should exist
        info = stream_file(repo, "p_item", path="new.txt")
        assert info["bytes"] == b"content"

    def test_move_file_source_not_found(self, repo: Path):
        with pytest.raises(FileNotFoundError):
            move_file(repo, "p_item", src="nope.txt", dst="dest.txt")

    def test_move_file_dst_exists_without_overwrite(self, repo: Path):
        upload_file(repo, "p_item", path="a.txt", file_bytes=b"aaa")
        upload_file(repo, "p_item", path="b.txt", file_bytes=b"bbb")
        with pytest.raises(FileExistsError):
            move_file(repo, "p_item", src="a.txt", dst="b.txt")

    def test_move_file_dst_is_directory_raises(self, repo: Path):
        upload_file(repo, "p_item", path="afile.txt", file_bytes=b"data")
        mkdir(repo, "p_item", path="adir")
        with pytest.raises(IsADirectoryError):
            move_file(repo, "p_item", src="afile.txt", dst="adir")

    def test_move_into_subdirectory(self, repo: Path):
        upload_file(repo, "p_item", path="flat.txt", file_bytes=b"data")
        mkdir(repo, "p_item", path="sub")
        result = move_file(repo, "p_item", src="flat.txt", dst="sub/flat.txt")
        assert result["dst"] == "sub/flat.txt"


# ---------------------------------------------------------------------------
# stream_file
# ---------------------------------------------------------------------------

class TestStreamFile:

    def test_stream_existing_file(self, repo: Path):
        upload_file(repo, "p_item", path="data.bin", file_bytes=b"\x00\x01\x02")
        result = stream_file(repo, "p_item", path="data.bin")
        assert result["filename"] == "data.bin"
        assert result["bytes"] == b"\x00\x01\x02"
        assert result["mimetype"] == "application/octet-stream"

    def test_stream_text_file_mime(self, repo: Path):
        upload_file(repo, "p_item", path="notes.txt", file_bytes=b"hello")
        result = stream_file(repo, "p_item", path="notes.txt")
        assert "text" in result["mimetype"]

    def test_stream_nonexistent_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError):
            stream_file(repo, "p_item", path="missing.pdf")
