from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone
import shutil
import hashlib
import yaml
import re

from .gitutils import git_commit_and_push, git_commit_paths
from .config import get_entity_field_specs_for_sfid, validate_sfid


# -------------------------------
# Canonical Entities API (SPEC v1)
#   - Canonical metadata lives under: entities/<sfid>/entity.yml
#   - No other module must modify these files
# -------------------------------


def _entities_dir(datarepo_path: Path) -> Path:
    p = datarepo_path / "entities"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _entity_file(datarepo_path: Path, sfid: str) -> Path:
    # Validate sfid conforms to SPEC (regex and safety)
    validate_sfid(sfid)
    return _entities_dir(datarepo_path) / sfid / "entity.yml"


def _read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _write_yaml(p: Path, data: dict) -> None:
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


# -------------------------------
# Validation helpers (type-aware via sfdatarepo.yml)
# -------------------------------
def _validate_against_specs(datarepo_path: Path, sfid: str, data: dict) -> None:
    """Validate entity data against repo-configured entity field specs.

    - Merges global entities.fields with per-type fields (type = sfid prefix before '_').
    - Enforces presence of required fields (if defined in specs).
    - Enforces regex for fields that are present in data and have a regex.
    - Unknown fields are allowed.
    """
    specs = get_entity_field_specs_for_sfid(sfid, datarepo_path)
    if not isinstance(specs, dict) or not specs:
        return  # no constraints configured
    # Required presence
    for fname, meta in specs.items():
        try:
            req = bool((meta or {}).get("required"))
        except Exception:
            req = False
        if req and fname not in data:
            raise ValueError(f"Missing required field: {fname}")
    # Regex checks for provided fields
    for fname, value in data.items():
        meta = specs.get(fname)
        if not isinstance(meta, dict):
            continue
        pattern = meta.get("regex")
        if pattern:
            s = "" if value is None else str(value)
            if re.fullmatch(pattern, s) is None:
                raise ValueError(f"Field '{fname}' does not match regex '{pattern}'")


# Public API

def list_entities(datarepo_path: Path) -> List[dict]:
    ents: List[dict] = []
    root = _entities_dir(datarepo_path)
    for d in sorted([p for p in root.iterdir() if p.is_dir()]):
        sfid = d.name
        fp = d / "entity.yml"
        if not fp.exists():
            continue
        try:
            data = _read_yaml(fp)
            if not isinstance(data, dict):
                data = {}
            data.setdefault("sfid", sfid)
            ents.append(data)
        except Exception:
            # Skip unreadable files
            continue
    return ents


def get_entity(datarepo_path: Path, sfid: str) -> dict:
    validate_sfid(sfid)
    fp = _entity_file(datarepo_path, sfid)
    if not fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    data = _read_yaml(fp)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("sfid", sfid)
    return data


def create_entity(datarepo_path: Path, sfid: str, fields: Optional[Dict] = None) -> dict:
    if not sfid:
        raise ValueError("sfid is required")
    validate_sfid(sfid)
    fp = _entity_file(datarepo_path, sfid)
    if fp.exists():
        raise FileExistsError(f"Entity '{sfid}' already exists")
    fp.parent.mkdir(parents=True, exist_ok=True)
    data: Dict = {}
    if fields:
        # Do not persist 'sfid' within entity.yml; identity is directory name
        data.update({k: v for k, v in fields.items() if k != "sfid"})
    # Validate against type-aware specs before writing
    _validate_against_specs(datarepo_path, sfid, data)
    # Ensure 'sfid' not written
    data_to_write = dict(data)
    data_to_write.pop("sfid", None)
    _write_yaml(fp, data_to_write)
    # Optional scaffold for parts (p_*) per PLM SPEC (files/, revisions/, refs/)
    # We create empty directories with .gitkeep files so Git tracks them.
    paths_to_commit = [fp]
    try:
        if sfid.startswith("p_"):
            root_dir = fp.parent
            # files subtree: do not pre-create any subdirectories under files/
            # The files/ root will be lazily created by file APIs when used.
            # revisions dir (no snapshots yet)
            revisions = root_dir / "revisions"
            revisions.mkdir(parents=True, exist_ok=True)
            rev_keep = revisions / ".gitkeep"
            if not rev_keep.exists():
                rev_keep.write_text("")
            paths_to_commit.append(rev_keep)
            # refs dir (no 'released' pointer yet)
            refs = root_dir / "refs"
            refs.mkdir(parents=True, exist_ok=True)
            refs_keep = refs / ".gitkeep"
            if not refs_keep.exists():
                refs_keep.write_text("")
            paths_to_commit.append(refs_keep)
    except Exception:
        # Non-fatal: scaffolding is optional; proceed with entity creation even if it fails.
        pass
    commit_msg = f"[smallFactory] Created entity {sfid}\n::sfid::{sfid}"
    # Commit entity.yml and any scaffold placeholders
    git_commit_paths(datarepo_path, paths_to_commit, commit_msg)
    data_ret = dict(data_to_write)
    data_ret["sfid"] = sfid
    return data_ret


# -------------------------------
# Revision management helpers (MVP)
# -------------------------------
def _next_revision_id(prev: Optional[str]) -> str:
    """Compute the next numeric revision id.

    - If prev is a number, increment numerically.
    - If prev is None or invalid/non-numeric, return '1'.
    """
    if prev is None:
        return "1"
    s = str(prev).strip()
    if not s.isdigit():
        return "1"
    try:
        return str(int(s) + 1)
    except Exception:
        return "1"


def _is_part_sfid(sfid: str) -> bool:
    return bool(sfid) and sfid.startswith("p_")


def _entity_dir(datarepo_path: Path, sfid: str) -> Path:
    return _entities_dir(datarepo_path) / sfid


def _revisions_dir(datarepo_path: Path, sfid: str) -> Path:
    return _entity_dir(datarepo_path, sfid) / "revisions"


def _refs_released_file(datarepo_path: Path, sfid: str) -> Path:
    return _entity_dir(datarepo_path, sfid) / "refs" / "released"


def _read_released_pointer(datarepo_path: Path, sfid: str) -> Optional[str]:
    p = _refs_released_file(datarepo_path, sfid)
    try:
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None
    return None


def _list_revision_meta_files(datarepo_path: Path, sfid: str) -> List[Tuple[str, Path]]:
    """Return list of (rev_label, meta_path) for existing snapshots."""
    out: List[Tuple[str, Path]] = []
    root = _revisions_dir(datarepo_path, sfid)
    if not root.exists() or not root.is_dir():
        return out
    for child in sorted([p for p in root.iterdir() if p.is_dir()]):
        meta = child / "meta.yml"
        if meta.exists():
            out.append((child.name, meta))
    return out


def _read_meta(meta_path: Path) -> dict:
    try:
        return _read_yaml(meta_path)
    except Exception:
        return {}


def get_revisions(datarepo_path: Path, sfid: str) -> Dict:
    """Return {rev, revisions[]} from filesystem snapshots per SPEC.

    - rev: contents of refs/released (label) or None if not set.
    - revisions: list of meta dicts augmented with 'id' and compatibility fields.
    """
    validate_sfid(sfid)
    # Ensure entity exists
    fp = _entity_file(datarepo_path, sfid)
    if not fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    released = _read_released_pointer(datarepo_path, sfid)
    metas = []
    for label, meta_path in _list_revision_meta_files(datarepo_path, sfid):
        m = _read_meta(meta_path) or {}
        # Normalize/compat fields expected by current UI
        m = dict(m)
        m.setdefault("id", label)
        # Map generated_at -> created_at for display compatibility
        if "created_at" not in m and "generated_at" in m:
            m["created_at"] = m.get("generated_at")
        metas.append(m)
    # Sort by created_at/generated_at then by label for stability
    def _ts(m):
        return m.get("created_at") or m.get("generated_at") or ""
    metas.sort(key=lambda x: (_ts(x), x.get("id", "")))
    return {"rev": released, "revisions": metas}


def _compute_next_label_from_fs(datarepo_path: Path, sfid: str) -> str:
    labels = [label for (label, _) in _list_revision_meta_files(datarepo_path, sfid)]
    # Numeric-only scheme: consider only numeric labels
    numeric_values = []
    for s in labels:
        s2 = str(s).strip()
        if s2.isdigit():
            try:
                numeric_values.append(int(s2))
            except Exception:
                pass
    if numeric_values:
        prev = str(max(numeric_values))
        return _next_revision_id(prev)
    # No numeric revisions yet -> start at 1 regardless of any alphabetic labels
    return _next_revision_id(None)


def cut_revision(
    datarepo_path: Path,
    sfid: str,
    rev: Optional[str] = None,
    *,
    notes: Optional[str] = None,
) -> dict:
    """Create a new draft snapshot under revisions/<rev>/ per SPEC.

    Fully self-contained snapshot: copies the entire entity directory except the
    'revisions' subtree. This includes entity.yml, refs/, files/, and any other
    files/directories under the entity.

    - Writes meta.yml with rev, status: draft, generated_at, notes?, source_commit?,
      and artifacts[] with sha256 for every copied file (paths are relative to snapshot root).
    - Does NOT flip refs/released.
    Returns: {sfid, rev, revisions} for UI compatibility.
    """
    validate_sfid(sfid)
    if not _is_part_sfid(sfid):
        raise ValueError("Revisions are only supported on part entities ('p_*')")
    # Ensure entity exists
    ent_fp = _entity_file(datarepo_path, sfid)
    if not ent_fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    # Validate entity.yml against specs (no-op if none configured)
    ent_data = _read_yaml(ent_fp) or {}
    _validate_against_specs(datarepo_path, sfid, ent_data)

    # Determine new rev label
    label = rev or _compute_next_label_from_fs(datarepo_path, sfid)
    snap_dir = _revisions_dir(datarepo_path, sfid) / label
    if snap_dir.exists():
        raise FileExistsError(f"Revision '{label}' already exists for {sfid}")
    (snap_dir).mkdir(parents=True, exist_ok=True)

    # Copy entire entity directory excluding the 'revisions' subtree
    artifacts: List[Dict] = []
    ent_dir = _entity_dir(datarepo_path, sfid)
    for child in ent_dir.iterdir():
        if child.name == "revisions":
            continue
        dest = snap_dir / child.name
        if child.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, dest)
        elif child.is_dir():
            shutil.copytree(child, dest)

    # Build and persist a resolved BOM tree for this snapshot before hashing artifacts
    try:
        bom_nodes = _build_bom_tree_nodes(datarepo_path, sfid)
        bom_doc = {
            "format": "bom_tree.v1",
            "root": sfid,
            "rev": label,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "nodes": bom_nodes,
        }
        _write_yaml(snap_dir / "bom_tree.yml", bom_doc)
    except Exception:
        # Non-fatal; if BOM cannot be generated, proceed without it
        pass

    # Record artifacts and hashes for all copied files (exclude meta.yml which we write later)
    for p in snap_dir.rglob("*"):
        if p.is_file():
            if p.name == ".gitkeep":
                continue
            rel = p.relative_to(snap_dir)
            rel_str = str(rel).replace("\\", "/")
            # Compute sha256
            h = hashlib.sha256()
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            # Classify a simple role for compatibility
            role = "file"
            if rel_str == "entity.yml":
                role = "entity"
            elif rel_str == "bom_tree.yml":
                role = "bom-tree"
            elif rel.parts and rel.parts[0] == "refs":
                role = "ref"
            else:
                # Subdir-agnostic best-effort classification by file type
                suffix = p.suffix.lower()
                if suffix in {".step", ".stp", ".iges", ".igs", ".stl", ".dxf", ".dwg", ".3mf", ".obj"}:
                    role = "cad-export"
                elif suffix in {".pdf", ".svg"}:
                    role = "drawing"
                elif suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff"}:
                    role = "image"
                elif suffix in {".md", ".txt"}:
                    role = "doc"
            artifacts.append({
                "role": role,
                "path": rel_str,
                "sha256": h.hexdigest(),
            })

    # Source commit (best-effort short SHA)
    source_commit = None
    try:
        import subprocess
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=datarepo_path, capture_output=True, text=True)
        if r.returncode == 0:
            source_commit = r.stdout.strip() or None
    except Exception:
        source_commit = None

    meta = {
        "rev": label,
        "status": "draft",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "id": label,  # compat for UI
    }
    if notes:
        meta["notes"] = str(notes)
    if source_commit:
        meta["source_commit"] = source_commit
    if artifacts:
        meta["artifacts"] = artifacts

    meta_fp = snap_dir / "meta.yml"
    _write_yaml(meta_fp, meta)

    # Commit the entire snapshot directory contents
    commit_paths = [snap_dir]
    msg = f"[smallFactory] Cut revision {sfid} {label}\n::sfid::{sfid}\n::sf-rev::{label}\n::sf-op::rev-cut"
    git_commit_paths(datarepo_path, commit_paths, msg)

    info = get_revisions(datarepo_path, sfid)
    return {"sfid": sfid, "rev": info.get("rev"), "revisions": info.get("revisions", [])}


# -------------------------------
# BOM traversal (resolved tree for snapshots)
# -------------------------------
def _resolve_rev_for_child(datarepo_path: Path, child_sfid: str, rev_spec) -> Optional[str]:
    """Resolve a child rev spec to an actual label.

    - If rev_spec is 'released' or falsy, return the current released pointer label (if any).
    - Otherwise return str(rev_spec).
    """
    try:
        s = ("" if rev_spec is None else str(rev_spec)).strip()
    except Exception:
        s = ""
    if not s or s.lower() == "released":
        try:
            return _read_released_pointer(datarepo_path, child_sfid)
        except Exception:
            return None
    return s


def _build_bom_tree_nodes(datarepo_path: Path, root_sfid: str, *, max_depth: Optional[int] = None) -> List[Dict]:
    """Walk the BOM starting at root_sfid and return flat list of resolved nodes.

    Node fields: parent, use, name, qty, rev_spec, rev (resolved), level, is_alt,
    alternates_group, cumulative_qty, cycle
    """
    nodes: List[Dict] = []

    def _get_name(sfid: str) -> str:
        try:
            ent = get_entity(datarepo_path, sfid)
            name = str(ent.get("name", sfid))
        except Exception:
            name = sfid
        return name

    def _to_int_or_none(val):
        try:
            if isinstance(val, bool):
                return None
            return int(val)
        except Exception:
            return None

    def recurse(parent_sfid: str, level: int, parent_mult: Optional[int], path_stack: List[str]):
        if max_depth is not None and level > max_depth:
            return
        try:
            lines = bom_list(datarepo_path, parent_sfid)
        except Exception:
            return
        for line in lines or []:
            if not isinstance(line, dict):
                continue
            child = str(line.get("use", "")).strip()
            if not child:
                continue
            qty = line.get("qty", 1)
            rev_spec = line.get("rev", "released")
            alts = line.get("alternates") if isinstance(line.get("alternates"), list) else []
            alt_group = line.get("alternates_group")
            qty_int = _to_int_or_none(qty)
            cum = qty_int if parent_mult is None else (qty_int * parent_mult if (qty_int is not None and parent_mult is not None) else None)
            cycle = child in path_stack
            node = {
                "parent": parent_sfid,
                "use": child,
                "name": _get_name(child),
                "qty": qty,
                "rev_spec": rev_spec,
                "rev": _resolve_rev_for_child(datarepo_path, child, rev_spec),
                "level": level,
                "is_alt": False,
                "alternates_group": alt_group,
                "cumulative_qty": cum,
                "cycle": bool(cycle),
            }
            nodes.append(node)
            if not cycle and child.startswith("p_"):
                recurse(child, level + 1, cum if (cum is not None) else parent_mult, path_stack + [child])
            for alt in alts:
                alt_use = str((alt or {}).get("use", "")).strip()
                if not alt_use:
                    continue
                alt_node = {
                    "parent": parent_sfid,
                    "use": alt_use,
                    "name": _get_name(alt_use),
                    "qty": qty,
                    "rev_spec": rev_spec,
                    "rev": _resolve_rev_for_child(datarepo_path, alt_use, rev_spec),
                    "level": level + 1,
                    "is_alt": True,
                    "alternates_group": alt_group,
                    "cumulative_qty": cum,
                    "cycle": alt_use in path_stack,
                }
                nodes.append(alt_node)
                if alt_use.startswith("p_") and alt_use not in path_stack:
                    recurse(alt_use, level + 2, cum if (cum is not None) else parent_mult, path_stack + [alt_use])

    recurse(root_sfid, 0, 1, [root_sfid])
    return nodes


def resolved_bom_tree(datarepo_path: Path, root_sfid: str, *, max_depth: Optional[int] = None) -> List[Dict]:
    """Public API: return resolved BOM nodes for a root SFID.

    Wrapper over internal traversal to be used by CLI/Web and other layers.
    """
    return _build_bom_tree_nodes(datarepo_path, root_sfid, max_depth=max_depth)


def bump_revision(datarepo_path: Path, sfid: str, *, notes: Optional[str] = None) -> dict:
    """Convenience: cut the next draft revision label and return revision info.

    This no longer flips the released pointer; it creates a draft snapshot per SPEC.
    """
    validate_sfid(sfid)
    if not _is_part_sfid(sfid):
        raise ValueError("Revisions are only supported on part entities ('p_*')")
    label = _compute_next_label_from_fs(datarepo_path, sfid)
    # Create the draft snapshot
    cut_revision(datarepo_path, sfid, label, notes=notes)
    # Return info plus the newly created label so callers can immediately release it
    info = get_revisions(datarepo_path, sfid)
    return {"sfid": sfid, "rev": info.get("rev"), "new_rev": label, "revisions": info.get("revisions", [])}


def release_revision(
    datarepo_path: Path,
    sfid: str,
    rev: str,
    *,
    released_at: Optional[str] = None,
    notes: Optional[str] = None,
) -> dict:
    """Mark snapshot revisions/<rev>/meta.yml as released and flip refs/released.

    - If released_at is None, uses now (UTC).
    - Updates meta.yml; writes refs/released with the label.
    Returns: {sfid, rev, revisions}
    """
    validate_sfid(sfid)
    if not _is_part_sfid(sfid):
        raise ValueError("Revisions are only supported on part entities ('p_*')")
    # Ensure snapshot exists
    snap_dir = _revisions_dir(datarepo_path, sfid) / rev
    meta_fp = snap_dir / "meta.yml"
    if not meta_fp.exists():
        raise FileNotFoundError(f"Revision '{rev}' not found for {sfid}")
    meta = _read_meta(meta_fp) or {}
    if released_at is None:
        released_at = datetime.now(timezone.utc).isoformat()
    meta["status"] = "released"
    meta["released_at"] = released_at
    if notes:
        meta["notes"] = str(notes)
    _write_yaml(meta_fp, meta)

    # Write refs/released pointer
    refs = _refs_released_file(datarepo_path, sfid)
    refs.parent.mkdir(parents=True, exist_ok=True)
    refs.write_text(str(rev) + "\n", encoding="utf-8")

    # Commit meta and pointer
    msg = f"[smallFactory] Release revision {sfid} {rev}\n::sfid::{sfid}\n::sf-rev::{rev}\n::sf-op::rev-release"
    git_commit_paths(datarepo_path, [meta_fp, refs], msg)

    info = get_revisions(datarepo_path, sfid)
    return {"sfid": sfid, "rev": info.get("rev"), "revisions": info.get("revisions", [])}

# -------------------------------
# BOM management helpers
# -------------------------------
def _ensure_part(datarepo_path: Path, parent_sfid: str) -> dict:
    """Load parent entity and ensure it is a part (sfid starts with 'p_')."""
    if not parent_sfid or not parent_sfid.startswith("p_"):
        raise ValueError("BOM is only supported on part entities ('p_*')")
    ent = get_entity(datarepo_path, parent_sfid)
    if not isinstance(ent, dict):
        ent = {"sfid": parent_sfid}
    return ent


def _bom_list_from_entity(ent: dict) -> List[dict]:
    bom = ent.get("bom")
    return list(bom) if isinstance(bom, list) else []


def bom_list(datarepo_path: Path, parent_sfid: str) -> List[dict]:
    ent = _ensure_part(datarepo_path, parent_sfid)
    return _bom_list_from_entity(ent)


def bom_add_line(
    datarepo_path: Path,
    parent_sfid: str,
    *,
    use: str,
    qty: int | str = 1,
    rev: str | None = "released",
    alternates: Optional[List[Dict]] = None,
    alternates_group: Optional[str] = None,
    index: Optional[int] = None,
    check_exists: bool = True,
) -> dict:
    """Add a BOM line to a part entity and commit the change.

    Returns a dict with keys: sfid, index, bom (updated list).
    """
    ent = _ensure_part(datarepo_path, parent_sfid)
    validate_sfid(use)
    if check_exists:
        fp = _entity_file(datarepo_path, use)
        if not fp.exists():
            raise FileNotFoundError(f"Referenced entity '{use}' does not exist under entities/")
    # Build line
    line: Dict = {"use": use}
    if qty is not None:
        line["qty"] = qty
    if rev:
        line["rev"] = rev
    if isinstance(alternates, list):
        line["alternates"] = alternates
    if alternates_group:
        line["alternates_group"] = alternates_group
    bom = _bom_list_from_entity(ent)
    # Insert
    if index is None or index >= len(bom):
        bom.append(line)
        ix = len(bom) - 1
    else:
        if index < 0:
            index = 0
        bom.insert(index, line)
        ix = index
    # Persist
    fp_parent = _entity_file(datarepo_path, parent_sfid)
    data_to_write = dict(ent)
    data_to_write.pop("sfid", None)
    data_to_write["bom"] = bom
    _write_yaml(fp_parent, data_to_write)
    msg = (
        f"[smallFactory] BOM add line to {parent_sfid} at index {ix}\n::sfid::{parent_sfid}\n::sf-op::bom-add\n::sf-child::{use}"
    )
    git_commit_and_push(datarepo_path, fp_parent, msg)
    return {"sfid": parent_sfid, "index": ix, "bom": bom}


def bom_remove_line(
    datarepo_path: Path,
    parent_sfid: str,
    *,
    index: Optional[int] = None,
    use: Optional[str] = None,
    remove_all: bool = False,
) -> dict:
    """Remove a BOM line by index or first/All matching use. Returns updated bom.
    Exactly one of index or use must be provided.
    """
    ent = _ensure_part(datarepo_path, parent_sfid)
    bom = _bom_list_from_entity(ent)
    if (index is None) == (use is None):
        raise ValueError("Provide exactly one of 'index' or 'use'")
    removed_indexes: List[int] = []
    if index is not None:
        if index < 0 or index >= len(bom):
            raise IndexError("index out of range")
        bom.pop(index)
        removed_indexes.append(index)
    else:
        # remove by use
        if not isinstance(use, str) or not use:
            raise ValueError("'use' must be a non-empty string")
        i = 0
        while i < len(bom):
            if isinstance(bom[i], dict) and bom[i].get("use") == use:
                bom.pop(i)
                removed_indexes.append(i)
                if not remove_all:
                    break
                # do not increment i; list shrank
                continue
            i += 1
        if not removed_indexes:
            raise ValueError(f"No BOM line found with use='{use}'")
    # Persist
    fp_parent = _entity_file(datarepo_path, parent_sfid)
    data_to_write = dict(ent)
    data_to_write.pop("sfid", None)
    if bom:
        data_to_write["bom"] = bom
    else:
        data_to_write.pop("bom", None)
    _write_yaml(fp_parent, data_to_write)
    msg = (
        f"[smallFactory] BOM remove line(s) from {parent_sfid} at {removed_indexes}\n::sfid::{parent_sfid}\n::sf-op::bom-remove"
    )
    git_commit_and_push(datarepo_path, fp_parent, msg)
    return {"sfid": parent_sfid, "removed": removed_indexes, "bom": bom}


def bom_set_line(
    datarepo_path: Path,
    parent_sfid: str,
    *,
    index: int,
    updates: Dict,
    check_exists: bool = True,
) -> dict:
    """Update fields on a BOM line by index. Returns updated line and bom."""
    ent = _ensure_part(datarepo_path, parent_sfid)
    bom = _bom_list_from_entity(ent)
    if index < 0 or index >= len(bom):
        raise IndexError("index out of range")
    line = dict(bom[index]) if isinstance(bom[index], dict) else {}
    # Allowed fields
    allowed = {"use", "qty", "rev", "alternates", "alternates_group"}
    for k in list(updates.keys()):
        if k not in allowed:
            raise ValueError(f"Unsupported BOM field: {k}")
    if "use" in updates:
        new_use = updates["use"]
        if not isinstance(new_use, str) or not new_use:
            raise ValueError("'use' must be a non-empty string")
        validate_sfid(new_use)
        if check_exists and not _entity_file(datarepo_path, new_use).exists():
            raise FileNotFoundError(f"Referenced entity '{new_use}' does not exist under entities/")
    if "alternates" in updates and updates["alternates"] is not None and not isinstance(updates["alternates"], list):
        raise ValueError("'alternates' must be a list of objects if provided")
    line.update({k: v for k, v in updates.items() if v is not None})
    bom[index] = line
    # Persist
    fp_parent = _entity_file(datarepo_path, parent_sfid)
    data_to_write = dict(ent)
    data_to_write.pop("sfid", None)
    data_to_write["bom"] = bom
    _write_yaml(fp_parent, data_to_write)
    msg = (
        f"[smallFactory] BOM edit line {index} on {parent_sfid}\n::sfid::{parent_sfid}\n::sf-op::bom-set"
    )
    git_commit_and_push(datarepo_path, fp_parent, msg)
    return {"sfid": parent_sfid, "index": index, "line": line, "bom": bom}


def bom_alt_add(
    datarepo_path: Path,
    parent_sfid: str,
    *,
    index: int,
    alt_use: str,
    check_exists: bool = True,
) -> dict:
    """Append an alternate to a BOM line's alternates list."""
    ent = _ensure_part(datarepo_path, parent_sfid)
    bom = _bom_list_from_entity(ent)
    if index < 0 or index >= len(bom):
        raise IndexError("index out of range")
    validate_sfid(alt_use)
    if check_exists and not _entity_file(datarepo_path, alt_use).exists():
        raise FileNotFoundError(f"Alternate entity '{alt_use}' does not exist under entities/")
    line = dict(bom[index]) if isinstance(bom[index], dict) else {}
    alts = line.get("alternates")
    if not isinstance(alts, list):
        alts = []
    alts.append({"use": alt_use})
    line["alternates"] = alts
    bom[index] = line
    # Persist
    fp_parent = _entity_file(datarepo_path, parent_sfid)
    data_to_write = dict(ent)
    data_to_write.pop("sfid", None)
    data_to_write["bom"] = bom
    _write_yaml(fp_parent, data_to_write)
    msg = (
        f"[smallFactory] BOM alt add on {parent_sfid} line {index}\n::sfid::{parent_sfid}\n::sf-op::bom-alt-add\n::sf-child::{alt_use}"
    )
    git_commit_and_push(datarepo_path, fp_parent, msg)
    return {"sfid": parent_sfid, "index": index, "line": line, "bom": bom}


def bom_alt_remove(
    datarepo_path: Path,
    parent_sfid: str,
    *,
    index: int,
    alt_index: Optional[int] = None,
    alt_use: Optional[str] = None,
) -> dict:
    """Remove an alternate by index or by alt_use from a BOM line."""
    ent = _ensure_part(datarepo_path, parent_sfid)
    bom = _bom_list_from_entity(ent)
    if index < 0 or index >= len(bom):
        raise IndexError("index out of range")
    line = dict(bom[index]) if isinstance(bom[index], dict) else {}
    alts = line.get("alternates")
    if not isinstance(alts, list) or not alts:
        raise ValueError("No alternates to remove")
    if (alt_index is None) == (alt_use is None):
        raise ValueError("Provide exactly one of 'alt_index' or 'alt_use'")
    removed = None
    if alt_index is not None:
        if alt_index < 0 or alt_index >= len(alts):
            raise IndexError("alt_index out of range")
        removed = alts.pop(alt_index)
    else:
        # by alt_use
        for i, a in enumerate(alts):
            if isinstance(a, dict) and a.get("use") == alt_use:
                removed = alts.pop(i)
                break
        if removed is None:
            raise ValueError(f"No alternate with use='{alt_use}' found")
    if alts:
        line["alternates"] = alts
    else:
        line.pop("alternates", None)
    bom[index] = line
    # Persist
    fp_parent = _entity_file(datarepo_path, parent_sfid)
    data_to_write = dict(ent)
    data_to_write.pop("sfid", None)
    data_to_write["bom"] = bom
    _write_yaml(fp_parent, data_to_write)
    msg = (
        f"[smallFactory] BOM alt remove on {parent_sfid} line {index}\n::sfid::{parent_sfid}\n::sf-op::bom-alt-remove"
    )
    git_commit_and_push(datarepo_path, fp_parent, msg)
    return {"sfid": parent_sfid, "index": index, "removed": removed, "line": line, "bom": bom}


def update_entity_field(datarepo_path: Path, sfid: str, field: str, value) -> dict:
    if not field or field == "sfid":
        raise ValueError("Invalid or immutable field: 'sfid'")
    validate_sfid(sfid)
    fp = _entity_file(datarepo_path, sfid)
    if not fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    data = _read_yaml(fp)
    if not isinstance(data, dict):
        data = {}
    data[field] = value
    # Validate entire record against specs post-update
    _validate_against_specs(datarepo_path, sfid, data)
    data_to_write = dict(data)
    data_to_write.pop("sfid", None)
    _write_yaml(fp, data_to_write)
    commit_msg = (
        f"[smallFactory] Updated entity {sfid} field {field}\n"
        f"::sfid::{sfid}\n::sf-field::{field}\n::sf-value::{value}"
    )
    git_commit_and_push(datarepo_path, fp, commit_msg)
    data_ret = dict(data_to_write)
    data_ret["sfid"] = sfid
    return data_ret


def update_entity_fields(datarepo_path: Path, sfid: str, updates: Dict) -> dict:
    if not isinstance(updates, dict) or not updates:
        raise ValueError("updates must be a non-empty dict")
    if "sfid" in updates:
        raise ValueError("Cannot update 'sfid' via this method")
    validate_sfid(sfid)
    fp = _entity_file(datarepo_path, sfid)
    if not fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    data = _read_yaml(fp)
    if not isinstance(data, dict):
        data = {}
    data.update(updates)
    # Validate merged record against type-aware specs
    _validate_against_specs(datarepo_path, sfid, data)
    data_to_write = dict(data)
    data_to_write.pop("sfid", None)
    _write_yaml(fp, data_to_write)
    # Summarize updated keys
    keys = ", ".join(sorted(updates.keys()))
    commit_msg = f"[smallFactory] Updated entity {sfid} fields: {keys}\n::sfid::{sfid}"
    git_commit_and_push(datarepo_path, fp, commit_msg)
    data_ret = dict(data_to_write)
    data_ret["sfid"] = sfid
    return data_ret


def delete_entity(datarepo_path: Path, sfid: str, *, force: bool = False) -> dict:
    """Hard delete is prohibited. Use retire_entity() instead.

    Entities are temporally unique and must not be removed from canonical history.
    """
    # Keep signature for backward-compatibility but disallow operation.
    raise RuntimeError(
        "Hard delete of entities is disabled. Use retire_entity(datarepo_path, sfid, reason=...) instead."
    )


def retire_entity(
    datarepo_path: Path,
    sfid: str,
    *,
    reason: Optional[str] = None,
    retired_at: Optional[str] = None,
) -> dict:
    """Soft-delete an entity by marking it retired in entities/<sfid>/entity.yml.

    - Sets fields: retired: true, retired_at: ISO-8601 UTC, retired_reason: <reason?>
    - Does not touch inventory; references remain valid historically.
    """
    validate_sfid(sfid)
    fp = _entity_file(datarepo_path, sfid)
    if not fp.exists():
        raise FileNotFoundError(f"Entity '{sfid}' not found")
    data = _read_yaml(fp)
    if not isinstance(data, dict):
        data = {}
    if retired_at is None:
        retired_at = datetime.now(timezone.utc).isoformat()
    data["retired"] = True
    data["retired_at"] = retired_at
    if reason:
        data["retired_reason"] = str(reason)
    data_to_write = dict(data)
    data_to_write.pop("sfid", None)
    _write_yaml(fp, data_to_write)
    # Commit
    base_msg = f"[smallFactory] Retired entity {sfid}\n::sfid::{sfid}\n::sf-retired::true"
    if reason:
        base_msg += f"\n::sf-reason::{reason}"
    git_commit_and_push(datarepo_path, fp, base_msg)
    data_ret = dict(data_to_write)
    data_ret["sfid"] = sfid
    return data_ret
