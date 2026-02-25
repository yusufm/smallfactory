from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple, Iterable
import io
import os
import shutil
import fnmatch
import mimetypes
import zipfile
from datetime import datetime
import subprocess

from .config import validate_sfid
from .gitutils import git_commit_paths


# -------------------------------
# Path helpers and safety
# -------------------------------

def _entities_dir(datarepo_path: Path) -> Path:
    p = datarepo_path / "entities"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _entity_dir(datarepo_path: Path, sfid: str) -> Path:
    validate_sfid(sfid)
    return _entities_dir(datarepo_path) / sfid


def _files_root(datarepo_path: Path, sfid: str) -> Path:
    """Return the working files root for an entity (files/ only)."""
    ent = _entity_dir(datarepo_path, sfid)
    root = ent / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_within(root: Path, rel_path: Optional[str]) -> Path:
    """Resolve rel_path under root with traversal protection. Returns absolute Path.

    - rel_path must be a relative path. '..' and absolute paths are rejected.
    - The resolved path must stay within root after normalization.
    - We resolve symlinks and verify containment to prevent escapes.
    """
    if rel_path in (None, "", "."):
        return root
    rp = str(rel_path).strip()
    if os.path.isabs(rp):
        raise ValueError("Absolute paths are not allowed")
    # Normalize to eliminate '../' etc.
    candidate = (root / rp).resolve()
    root_res = root.resolve()
    try:
        candidate.relative_to(root_res)
    except Exception:
        raise ValueError("Path escapes the files root scope")
    return candidate


# -------------------------------
# Listing
# -------------------------------

def list_files(
    datarepo_path: Path,
    sfid: str,
    *,
    path: Optional[str] = None,
    recursive: bool = False,
    glob: Optional[str] = None,
) -> Dict:
    """List files and folders under entities/<sfid>/files[/path].

    Returns dict: { "sfid", "path", "items": [ {type, name, path, size, mtime} ] }
    - type: 'file' or 'dir'
    - path: POSIX-style path relative to the files root
    """
    root = _files_root(datarepo_path, sfid)
    base = _resolve_within(root, path)
    if not base.exists():
        return {"sfid": sfid, "path": path or "", "items": []}

    items: List[Dict] = []
    def _add(p: Path):
        rel = p.relative_to(root)
        rel_str = str(rel).replace("\\", "/")
        name = p.name
        if glob and not fnmatch.fnmatch(rel_str, glob):
            return
        if p.is_dir():
            items.append({
                "type": "dir",
                "name": name,
                "path": rel_str,
            })
        elif p.is_file():
            # Skip control files
            if name == ".gitkeep":
                return
            st = p.stat()
            items.append({
                "type": "file",
                "name": name,
                "path": rel_str,
                "size": int(st.st_size),
                "mtime": datetime.fromtimestamp(st.st_mtime).isoformat(),
            })

    if base.is_dir():
        if recursive:
            for p in sorted(base.rglob("*")):
                _add(p)
        else:
            for p in sorted(base.iterdir()):
                _add(p)
    elif base.is_file():
        _add(base)
    else:
        # unknown special; ignore
        pass

    return {"sfid": sfid, "path": path or "", "items": items}


# -------------------------------
# Mutations (files area only)
# -------------------------------

def mkdir(
    datarepo_path: Path,
    sfid: str,
    *,
    path: str,
) -> Dict:
    """Create a folder in the files area and place a .gitkeep so git tracks it."""
    root = _files_root(datarepo_path, sfid)
    target = _resolve_within(root, path)
    if root == target:
        raise ValueError("Refusing to create the files root itself")
    target.mkdir(parents=True, exist_ok=True)
    keep = target / ".gitkeep"
    did_create = False
    if not keep.exists():
        keep.write_text("")
        did_create = True
    # Auto-commit only if we actually created a placeholder to stage
    if did_create:
        rel = str(target.relative_to(root)).replace("\\", "/")
        msg = (
            f"[smallFactory] files-mkdir {sfid}\n::sfid::{sfid}\n::sf-op::files-mkdir\npath=files/{rel}"
        )
        git_commit_paths(datarepo_path, [keep], msg)
    return {"sfid": sfid, "path": str(target.relative_to(root)).replace("\\", "/")}


def rmdir(
    datarepo_path: Path,
    sfid: str,
    *,
    path: str,
) -> Dict:
    """Delete an empty folder. Empty means only optional .gitkeep present."""
    root = _files_root(datarepo_path, sfid)
    target = _resolve_within(root, path)
    if not target.exists() or not target.is_dir():
        raise FileNotFoundError("Folder does not exist")
    if target == root:
        raise ValueError("Cannot remove the files root")
    # Determine emptiness ignoring .gitkeep
    children = [p for p in target.iterdir() if p.name != ".gitkeep"]
    if children:
        raise OSError("Folder is not empty")
    # Remove .gitkeep then the dir
    keep = target / ".gitkeep"
    if keep.exists():
        # Stage deletion of .gitkeep and commit
        rel = str(target.relative_to(root)).replace("\\", "/")
        msg = (
            f"[smallFactory] files-rmdir {sfid}\n::sfid::{sfid}\n::sf-op::files-rmdir\npath=files/{rel}"
        )
        git_commit_paths(datarepo_path, [keep], msg, delete=True)
    # Remove the now-empty directory from working tree.
    # Be tolerant if the directory disappeared after committing .gitkeep deletion
    # (e.g., due to concurrent operations or tooling behavior):
    if target.exists():
        try:
            target.rmdir()
        except FileNotFoundError:
            # Already removed; treat as success
            pass
    return {"sfid": sfid, "removed": str(target.relative_to(root)).replace("\\", "/")}


def upload_file(
    datarepo_path: Path,
    sfid: str,
    *,
    path: str,
    file_bytes: bytes,
    overwrite: bool = False,
) -> Dict:
    """Upload (write) a file to the files area at path. Creates parents as needed."""
    root = _files_root(datarepo_path, sfid)
    dest = _resolve_within(root, path)
    if dest.exists() and dest.is_dir():
        raise IsADirectoryError("Destination is a directory")
    if dest.exists() and not overwrite:
        raise FileExistsError("File already exists (use overwrite=True)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as f:
        f.write(file_bytes)
    # Auto-commit new/updated file
    rel = str(dest.relative_to(root)).replace("\\", "/")
    msg = (
        f"[smallFactory] files-add {sfid}\n::sfid::{sfid}\n::sf-op::files-add\npath=files/{rel}"
    )
    git_commit_paths(datarepo_path, [dest], msg)
    return {"sfid": sfid, "path": str(dest.relative_to(root)).replace("\\", "/"), "size": len(file_bytes)}


def delete_file(
    datarepo_path: Path,
    sfid: str,
    *,
    path: str,
) -> Dict:
    root = _files_root(datarepo_path, sfid)
    target = _resolve_within(root, path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("File not found")
    # Auto-commit deletion (stage via git rm)
    rel = str(target.relative_to(root)).replace("\\", "/")
    msg = (
        f"[smallFactory] files-rm {sfid}\n::sfid::{sfid}\n::sf-op::files-rm\npath=files/{rel}"
    )
    git_commit_paths(datarepo_path, [target], msg, delete=True)
    return {"sfid": sfid, "removed": str(target.relative_to(root)).replace("\\", "/")}


def move_file(
    datarepo_path: Path,
    sfid: str,
    *,
    src: str,
    dst: str,
    overwrite: bool = False,
) -> Dict:
    root = _files_root(datarepo_path, sfid)
    src_p = _resolve_within(root, src)
    dst_p = _resolve_within(root, dst)
    if not src_p.exists() or not src_p.is_file():
        raise FileNotFoundError("Source file not found")
    if dst_p.exists():
        if dst_p.is_dir():
            raise IsADirectoryError("Destination is a directory")
        if not overwrite:
            raise FileExistsError("Destination exists (use overwrite=True)")
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    # Use git mv so the rename is staged properly
    subprocess.run(["git", "mv", str(src_p), str(dst_p)], cwd=datarepo_path, check=True)
    # Commit the staged rename by adding destination (harmless if already staged)
    rel_src = str(src_p.relative_to(root)).replace("\\", "/")
    rel_dst = str(dst_p.relative_to(root)).replace("\\", "/")
    msg = (
        f"[smallFactory] files-mv {sfid}\n::sfid::{sfid}\n::sf-op::files-mv\nsrc=files/{rel_src} dst=files/{rel_dst}"
    )
    git_commit_paths(datarepo_path, [dst_p], msg)
    return {
        "sfid": sfid,
        "src": str(src_p.relative_to(root)).replace("\\", "/"),
        "dst": str(dst_p.relative_to(root)).replace("\\", "/"),
    }


def move_dir(
    datarepo_path: Path,
    sfid: str,
    *,
    src: str,
    dst: str,
    overwrite: bool = False,
) -> Dict:
    root = _files_root(datarepo_path, sfid)
    src_p = _resolve_within(root, src)
    dst_p = _resolve_within(root, dst)
    if not src_p.exists() or not src_p.is_dir():
        raise FileNotFoundError("Source folder not found")
    if src_p == root:
        raise ValueError("Cannot move the files root")
    if dst_p.exists():
        if dst_p.is_file():
            raise NotADirectoryError("Destination is a file")
        if not overwrite:
            raise FileExistsError("Destination folder exists (use overwrite=True)")
        # If overwrite, ensure the destination is empty
        if any(dst_p.iterdir()):
            raise OSError("Destination folder is not empty")
    else:
        dst_p.parent.mkdir(parents=True, exist_ok=True)
    # Use git mv for directories as well
    subprocess.run(["git", "mv", str(src_p), str(dst_p)], cwd=datarepo_path, check=True)
    rel_src = str(src_p.relative_to(root)).replace("\\", "/")
    rel_dst = str(dst_p.relative_to(root)).replace("\\", "/")
    msg = (
        f"[smallFactory] files-mv {sfid}\n::sfid::{sfid}\n::sf-op::files-mv\nsrc=files/{rel_src} dst=files/{rel_dst}"
    )
    git_commit_paths(datarepo_path, [dst_p], msg)
    return {
        "sfid": sfid,
        "src": str(src_p.relative_to(root)).replace("\\", "/"),
        "dst": str(dst_p.relative_to(root)).replace("\\", "/"),
    }


# -------------------------------
# Downloads
# -------------------------------

def stream_file(
    datarepo_path: Path,
    sfid: str,
    *,
    path: str,
) -> Dict:
    """Return in-memory bytes and metadata for a files-area file."""
    root = _files_root(datarepo_path, sfid)
    p = _resolve_within(root, path)
    if not p.exists() or not p.is_file():
        raise FileNotFoundError("File not found")
    b = p.read_bytes()
    mt, _ = mimetypes.guess_type(p.name)
    return {"filename": p.name, "mimetype": mt or "application/octet-stream", "bytes": b}


def zip_files(
    datarepo_path: Path,
    sfid: str,
    *,
    paths: Iterable[str],
) -> bytes:
    """Return a zip archive (bytes) containing the requested files/folders.

    - Folder entries include their entire contents.
    - Paths are validated and must reside under the files root.
    """
    root = _files_root(datarepo_path, sfid)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in paths:
            p = _resolve_within(root, rel)
            if not p.exists():
                continue
            if p.is_file():
                arc = str(p.relative_to(root)).replace("\\", "/")
                if p.name == ".gitkeep":
                    continue
                zf.write(p, arcname=arc)
            else:
                for child in p.rglob("*"):
                    if child.is_file():
                        if child.name == ".gitkeep":
                            continue
                        arc = str(child.relative_to(root)).replace("\\", "/")
                        zf.write(child, arcname=arc)
    zf.close()
    buf.seek(0)
    return buf.getvalue()
