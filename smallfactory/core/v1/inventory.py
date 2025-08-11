from __future__ import annotations
from pathlib import Path
import json
import yaml
import time
import os
import random
from typing import Optional, List, Dict, Tuple
from collections import defaultdict

from .gitutils import git_commit_and_push, git_commit_paths
from .config import validate_sfid


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inventory_dir = datarepo_path / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    return inventory_dir


# -------------------------------
# Helpers for SPEC v0.1 inventory
# - inventory/<part_sfid>/journal.ndjson (append-only)
# - inventory/<part_sfid>/onhand.generated.yml (per-part cache)
# - inventory/_location/<location_sfid>/onhand.generated.yml (reverse cache)
# - entities/<sfid>/entity.yml (canonical metadata)
# -------------------------------

def _entities_dir(datarepo_path: Path) -> Path:
    p = datarepo_path / "entities"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _entity_file(datarepo_path: Path, sfid: str) -> Path:
    return _entities_dir(datarepo_path) / sfid / "entity.yml"


def _entity_exists(datarepo_path: Path, sfid: str) -> bool:
    return _entity_file(datarepo_path, sfid).exists()


def _validate_location_sfid(location_sfid: str) -> None:
    """Validate that a location identifier is a proper location sfid per SPEC (prefix l_)."""
    validate_sfid(location_sfid)
    if not location_sfid.startswith("l_"):
        raise ValueError("location must be a valid location sfid starting with 'l_'")


def _part_dir(datarepo_path: Path, part_sfid: str) -> Path:
    validate_sfid(part_sfid)
    if not part_sfid.startswith("p_"):
        # Allow any part-like sfid; SPEC recognizes p_ for parts in v0.1
        pass
    d = ensure_inventory_dir(datarepo_path) / part_sfid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _journal_file(datarepo_path: Path, part_sfid: str) -> Path:
    return _part_dir(datarepo_path, part_sfid) / "journal.ndjson"


def _part_onhand_file(datarepo_path: Path, part_sfid: str) -> Path:
    return _part_dir(datarepo_path, part_sfid) / "onhand.generated.yml"


def _location_cache_file(datarepo_path: Path, location_sfid: str) -> Path:
    _validate_location_sfid(location_sfid)
    d = ensure_inventory_dir(datarepo_path) / "_location" / location_sfid
    d.mkdir(parents=True, exist_ok=True)
    return d / "onhand.generated.yml"


def _read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _write_yaml(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _read_lines(p: Path) -> List[str]:
    if not p.exists():
        return []
    with open(p, "r") as f:
        return [line.rstrip("\n") for line in f]


def _append_line(p: Path, line: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as f:
        f.write(line + "\n")


_CROCKFORD32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _to_base32(data: bytes) -> str:
    # Simple Base32 (Crockford) encode without padding; only for fixed ULID 16 bytes
    bits = 0
    value = 0
    out = []
    for b in data:
        value = (value << 8) | b
        bits += 8
        while bits >= 5:
            out.append(_CROCKFORD32[(value >> (bits - 5)) & 0x1F])
            bits -= 5
    if bits:
        out.append(_CROCKFORD32[(value << (5 - bits)) & 0x1F])
    return "".join(out)


def _new_ulid() -> str:
    """Generate a 26-char Crockford Base32 ULID string.
    Time component is milliseconds since epoch (48 bits), plus 80 bits of randomness.
    """
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(6, "big")  # 48 bits
    rand_bytes = os.urandom(10)  # 80 bits
    return _to_base32(ts_bytes + rand_bytes)[:26]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_entity_meta(datarepo_path: Path, sfid: str) -> Dict:
    p = _entity_file(datarepo_path, sfid)
    if not p.exists():
        return {}
    try:
        return _read_yaml(p)
    except Exception:
        return {}


def _default_location(datarepo_path: Path) -> Optional[str]:
    cfg = ensure_inventory_dir(datarepo_path) / "config.yml"
    if not cfg.exists():
        return None
    try:
        v = _read_yaml(cfg).get("default_location")
        if isinstance(v, str) and v:
            _validate_location_sfid(v)
            return v
    except Exception:
        return None
    return None


def _compute_part_onhand_from_journal(journal_path: Path) -> Tuple[Dict[str, int], int]:
    by_loc: Dict[str, int] = defaultdict(int)
    total = 0
    if not journal_path.exists():
        return {}, 0
    for line in _read_lines(journal_path):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
            loc = str(obj.get("location", "")).strip()
            if not loc:
                # skip entries without a location (should be defaulted at write time)
                continue
            qty_delta = int(obj.get("qty_delta", 0))
            by_loc[loc] += qty_delta
            total += qty_delta
        except Exception:
            # ignore malformed lines
            continue
    # drop zero entries
    by_loc = {k: v for k, v in by_loc.items() if v != 0}
    return by_loc, total


def _write_part_cache(datarepo_path: Path, part_sfid: str) -> Dict:
    journal = _journal_file(datarepo_path, part_sfid)
    by_loc, total = _compute_part_onhand_from_journal(journal)
    ent_meta = _read_entity_meta(datarepo_path, part_sfid)
    uom = ent_meta.get("uom", "ea")
    data = {
        "uom": uom,
        "as_of": _now_iso(),
        "by_location": dict(sorted(by_loc.items())),
        "total": int(total),
    }
    _write_yaml(_part_onhand_file(datarepo_path, part_sfid), data)
    return data


def _write_location_cache(datarepo_path: Path, location_sfid: str) -> Dict:
    # Build from all per-part caches
    inv_dir = ensure_inventory_dir(datarepo_path)
    parts: Dict[str, int] = {}
    uom = "ea"
    for pdir in sorted([p for p in inv_dir.iterdir() if p.is_dir() and p.name != "_location"]):
        part = pdir.name
        cache_p = _part_onhand_file(datarepo_path, part)
        if not cache_p.exists():
            # Try to compute if journal exists
            if _journal_file(datarepo_path, part).exists():
                _write_part_cache(datarepo_path, part)
            else:
                continue
        try:
            cache = _read_yaml(cache_p)
        except Exception:
            continue
        qty = int(cache.get("by_location", {}).get(location_sfid, 0))
        if qty:
            parts[part] = qty
        # Keep last non-empty uom seen
        uom = cache.get("uom", uom) or uom
    total = int(sum(parts.values()))
    data = {
        "uom": uom,
        "as_of": _now_iso(),
        "parts": dict(sorted(parts.items())),
        "total": total,
    }
    _write_yaml(_location_cache_file(datarepo_path, location_sfid), data)
    return data


def inventory_post(
    datarepo_path: Path,
    part: str,
    qty_delta: int,
    location: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict:
    """Append an inventory journal entry per SPEC.

    - Writes to inventory/<part>/journal.ndjson as one JSON object per line.
    - Updates per-part onhand cache and per-location reverse cache.
    - Commit message must include ::sfid::<PART> and ::sfid::<LOCATION> tokens.
    """
    validate_sfid(part)
    if not _entity_exists(datarepo_path, part):
        raise FileNotFoundError(f"Part sfid '{part}' does not exist under entities/")
    if location is None or not str(location).strip():
        location = _default_location(datarepo_path)
    if not location:
        raise ValueError("location is required (or configure inventory/config.yml: default_location)")
    _validate_location_sfid(location)
    if not _entity_exists(datarepo_path, location):
        raise FileNotFoundError(f"Location sfid '{location}' does not exist under entities/")
    try:
        delta = int(qty_delta)
    except Exception:
        raise ValueError("qty_delta must be an integer")
    if delta == 0:
        raise ValueError("qty_delta must be non-zero")

    # Append NDJSON line
    entry = {
        "txn": _new_ulid(),
        "location": location,
        "qty_delta": delta,
    }
    if reason is not None:
        entry["reason"] = str(reason)
    journal = _journal_file(datarepo_path, part)
    _append_line(journal, json.dumps(entry, separators=(",", ":")))

    # Update caches
    part_cache = _write_part_cache(datarepo_path, part)
    loc_cache = _write_location_cache(datarepo_path, location)

    # Commit journal and caches together
    paths = [journal, _part_onhand_file(datarepo_path, part), _location_cache_file(datarepo_path, location)]
    msg = (
        f"[smallFactory] Inventory post for {part} at {location} qty_delta {delta}\n"
        f"::sfid::{part}\n::sfid::{location}\n::sf-delta::{delta}"
    )
    git_commit_paths(datarepo_path, paths, msg)

    return {
        "part": part,
        "location": location,
        "qty_delta": delta,
        "txn": entry["txn"],
        "onhand": part_cache,
    }


def inventory_onhand(
    datarepo_path: Path,
    part: Optional[str] = None,
    location: Optional[str] = None,
) -> Dict:
    """Report on-hand quantities.

    - If part is provided: return per-part onhand cache (compute if missing).
    - Else if location is provided: return per-location reverse cache (compute from part caches).
    - Else: return summary over all parts (from caches; compute missing from journals).
    """
    inv_dir = ensure_inventory_dir(datarepo_path)
    if part:
        validate_sfid(part)
        if not _entity_exists(datarepo_path, part):
            raise FileNotFoundError(f"Part sfid '{part}' does not exist under entities/")
        cache_p = _part_onhand_file(datarepo_path, part)
        if not cache_p.exists():
            _write_part_cache(datarepo_path, part)
        return _read_yaml(cache_p)

    if location:
        _validate_location_sfid(location)
        if not _entity_exists(datarepo_path, location):
            raise FileNotFoundError(f"Location sfid '{location}' does not exist under entities/")
        cache_p = _location_cache_file(datarepo_path, location)
        # Recompute from part caches
        return _write_location_cache(datarepo_path, location)

    # Summary over all parts
    parts = []
    grand_total = 0
    for pdir in sorted([p for p in inv_dir.iterdir() if p.is_dir() and p.name != "_location"]):
        part_sfid = pdir.name
        cache_p = _part_onhand_file(datarepo_path, part_sfid)
        if not cache_p.exists():
            if _journal_file(datarepo_path, part_sfid).exists():
                _write_part_cache(datarepo_path, part_sfid)
            else:
                continue
        try:
            cache = _read_yaml(cache_p)
        except Exception:
            continue
        parts.append({
            "sfid": part_sfid,
            "uom": cache.get("uom", "ea"),
            "total": int(cache.get("total", 0)),
        })
        grand_total += int(cache.get("total", 0))
    return {"parts": parts, "total": grand_total}


def inventory_rebuild(datarepo_path: Path) -> Dict:
    """Rebuild all onhand caches from journals (per-part and per-location)."""
    inv_dir = ensure_inventory_dir(datarepo_path)
    # Rebuild per-part
    rebuilt_parts: List[str] = []
    for pdir in sorted([p for p in inv_dir.iterdir() if p.is_dir() and p.name != "_location"]):
        part_sfid = pdir.name
        if _journal_file(datarepo_path, part_sfid).exists():
            _write_part_cache(datarepo_path, part_sfid)
            rebuilt_parts.append(part_sfid)
    # Rebuild per-location based on all part caches
    locations: set[str] = set()
    for pdir in sorted([p for p in inv_dir.iterdir() if p.is_dir() and p.name != "_location"]):
        cache_p = _part_onhand_file(datarepo_path, pdir.name)
        if not cache_p.exists():
            continue
        try:
            cache = _read_yaml(cache_p)
        except Exception:
            continue
        locations.update(cache.get("by_location", {}).keys())
    rebuilt_locations: List[str] = []
    for loc in sorted(locations):
        _write_location_cache(datarepo_path, loc)
        rebuilt_locations.append(loc)
    # Commit touched caches in batches
    to_commit: List[Path] = []
    for part in rebuilt_parts:
        to_commit.append(_part_onhand_file(datarepo_path, part))
    for loc in rebuilt_locations:
        to_commit.append(_location_cache_file(datarepo_path, loc))
    if to_commit:
        git_commit_paths(
            datarepo_path,
            to_commit,
            "[smallFactory] Rebuilt inventory onhand caches\n::sf-action::rebuild",
        )
    return {"parts": rebuilt_parts, "locations": rebuilt_locations}
