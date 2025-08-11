from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone
import yaml
import re

from .gitutils import git_commit_and_push
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
    commit_msg = f"[smallFactory] Created entity {sfid}\n::sfid::{sfid}"
    git_commit_and_push(datarepo_path, fp, commit_msg)
    data_ret = dict(data_to_write)
    data_ret["sfid"] = sfid
    return data_ret


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
