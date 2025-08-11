from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone
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
    # Optional scaffold for parts (p_*) per PLM SPEC (design/, revisions/, refs/)
    # We create empty directories with .gitkeep files so Git tracks them.
    paths_to_commit = [fp]
    try:
        if sfid.startswith("p_"):
            root_dir = fp.parent
            # design subtree
            design = root_dir / "design"
            for sub in (design / "src", design / "exports", design / "docs"):
                sub.mkdir(parents=True, exist_ok=True)
                keep = sub / ".gitkeep"
                if not keep.exists():
                    keep.write_text("")
                paths_to_commit.append(keep)
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
