from __future__ import annotations
from pathlib import Path
import yaml
import json
import re
from typing import Optional, List, Dict
from collections import defaultdict

from .gitutils import git_commit_and_push, git_commit_paths
from .config import get_inventory_field_specs


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inventory_dir = datarepo_path / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    return inventory_dir


# -------------------------------
# Helpers for SPEC v1 layout
# inventory/<l_*>/<SFID>.yml
# -------------------------------

def _entities_dir(datarepo_path: Path) -> Path:
    p = datarepo_path / "entities"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _entity_file(datarepo_path: Path, sfid: str) -> Path:
    return _entities_dir(datarepo_path) / f"{sfid}.yml"


def _entity_exists(datarepo_path: Path, sfid: str) -> bool:
    return _entity_file(datarepo_path, sfid).exists()


def _validate_location_name(name: str) -> None:
    """Ensure name is a safe file name without slugifying.

    Allowed: A-Z a-z 0-9 . _ - (no path separators)."""
    if not name or name in {".", ".."}:
        raise ValueError("name must be a non-empty value")
    if "/" in name or "\\" in name:
        raise ValueError("name cannot contain path separators")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]+", name):
        raise ValueError("name contains invalid characters; allowed: letters, numbers, space, . _ -")


def _validate_location_sfid(location_sfid: str) -> None:
    """Validate that a location identifier is a proper location sfid and safe as a directory name."""
    _validate_location_name(location_sfid)
    if not location_sfid.startswith("l_"):
        raise ValueError("location must be a valid location sfid starting with 'l_'")


def _location_dir(datarepo_path: Path, location_sfid: str) -> Path:
    _validate_location_sfid(location_sfid)
    return ensure_inventory_dir(datarepo_path) / location_sfid


def _inventory_file(datarepo_path: Path, location_sfid: str, sfid: str) -> Path:
    # sfid must be safe as a filename; rely on global sfid rules, but enforce no path separators
    if "/" in sfid or "\\" in sfid:
        raise ValueError("sfid cannot contain path separators")
    return _location_dir(datarepo_path, location_sfid) / f"{sfid}.yml"


def _read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _write_yaml(p: Path, data: dict) -> None:
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def add_item(datarepo_path: Path, item: dict) -> dict:
    """Create or stage inventory for an entity at a specific location per SPEC.

    Required inputs:
      - item['sfid']: entity sfid (e.g., 'p_m3x10')
      - item['location']: location sfid (e.g., 'l_a1')
      - item['quantity']: non-negative integer

    This writes inventory/<location>/<sfid>.yml with at least {'quantity': <int>}.
    Other keys are preserved if provided, but are non-canonical.
    """
    specs = get_inventory_field_specs()
    # Validate quantity against specs if present, otherwise basic int >= 0
    if "quantity" not in item:
        raise ValueError("Missing required field: quantity")
    try:
        quantity = int(item["quantity"])
    except Exception:
        raise ValueError("quantity must be an integer")
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    # Validate presence of sfid and location
    sfid = str(item.get("sfid", "")).strip()
    if not sfid:
        raise ValueError("Missing required field: sfid (entity identifier)")
    location = str(item.get("location", "")).strip()
    if not location:
        raise ValueError("Missing required field: location (location sfid)")
    _validate_location_sfid(location)
    # Enforce existence of referenced entities
    if not _entity_exists(datarepo_path, sfid):
        raise FileNotFoundError(f"Entity sfid '{sfid}' does not exist under entities/")
    if not _entity_exists(datarepo_path, location):
        raise FileNotFoundError(f"Location sfid '{location}' does not exist under entities/")

    # Determine inventory file path
    inv_file = _inventory_file(datarepo_path, location, sfid)
    if inv_file.exists():
        raise FileExistsError(
            f"Inventory file already exists for sfid '{sfid}' at location '{location}'. Use inventory adjust instead."
        )
    inv_file.parent.mkdir(parents=True, exist_ok=True)

    # Compose data: required quantity + preserve provided optional keys (excluding routing keys)
    extras = {k: v for k, v in item.items() if k not in {"sfid", "location"}}
    data = {**extras, "quantity": quantity}
    _write_yaml(inv_file, data)

    commit_lines = [
        f"[smallfactory] Added inventory entry for {sfid} at {location} with quantity {quantity}",
        f"::sfid::{sfid}",
        f"::sfid::{location}",
    ]
    git_commit_and_push(datarepo_path, inv_file, "\n".join(commit_lines))

    # Return a flattened view for CLI: include entity name if available
    name = ""
    ent_meta_path = _entity_file(datarepo_path, sfid)
    try:
        ent_meta = _read_yaml(ent_meta_path)
        name = ent_meta.get("name", "")
    except Exception:
        name = ""
    return {"sfid": sfid, "name": name, "quantity": quantity, "location": location}


def list_items(datarepo_path: Path) -> list[dict]:
    inventory_dir = ensure_inventory_dir(datarepo_path)
    # Aggregate by entity sfid across all location directories
    totals: Dict[str, int] = defaultdict(int)
    locs: Dict[str, List[str]] = defaultdict(list)

    for loc_dir in sorted([p for p in inventory_dir.iterdir() if p.is_dir()]):
        location = loc_dir.name
        if not location.startswith("l_"):
            # Skip non-compliant directories
            continue
        for inv_file in sorted(loc_dir.glob("*.yml")):
            sfid = inv_file.stem
            try:
                data = _read_yaml(inv_file)
                qty = int(data.get("quantity", 0))
            except Exception:
                qty = 0
            totals[sfid] += qty
            locs[sfid].append(location)

    # Compose results; include entity name from entities/<sfid>.yml if present
    results: List[Dict] = []
    for sfid in sorted(totals.keys()):
        name = ""
        ent_meta_path = _entity_file(datarepo_path, sfid)
        if ent_meta_path.exists():
            try:
                name = _read_yaml(ent_meta_path).get("name", "")
            except Exception:
                name = ""
        locations = sorted(set(locs[sfid]))
        summary = {
            "sfid": sfid,
            "name": name,
            "quantity": totals[sfid],
            "location": locations[0] if len(locations) == 1 else ("multiple" if locations else ""),
            "locations": locations,
        }
        results.append(summary)
    return results


def view_item(datarepo_path: Path, sfid: str) -> dict:
    # Aggregate across all inventory/<l_*>/<sfid>.yml files
    inventory_dir = ensure_inventory_dir(datarepo_path)
    total_qty = 0
    locations: Dict[str, int] = {}
    for loc_dir in sorted([p for p in inventory_dir.iterdir() if p.is_dir()]):
        location = loc_dir.name
        if not location.startswith("l_"):
            continue
        inv_file = loc_dir / f"{sfid}.yml"
        if not inv_file.exists():
            continue
        data = _read_yaml(inv_file)
        qty = int(data.get("quantity", 0))
        locations[location] = qty
        total_qty += qty
    if total_qty == 0 and not locations:
        # Treat as not found in inventory context
        raise FileNotFoundError(f"Inventory item '{sfid}' not found")
    name = ""
    ent_meta_path = _entity_file(datarepo_path, sfid)
    if ent_meta_path.exists():
        try:
            name = _read_yaml(ent_meta_path).get("name", "")
        except Exception:
            name = ""
    return {"sfid": sfid, "name": name, "quantity": total_qty, "locations": locations}


def delete_item(datarepo_path: Path, sfid: str) -> dict:
    """Remove all inventory files for the given entity across all locations.

    Does NOT delete the canonical entity file under entities/.
    """
    inventory_dir = ensure_inventory_dir(datarepo_path)
    to_delete: List[Path] = []
    affected_locations: List[str] = []
    for loc_dir in sorted([p for p in inventory_dir.iterdir() if p.is_dir()]):
        location = loc_dir.name
        if not location.startswith("l_"):
            continue
        inv_file = loc_dir / f"{sfid}.yml"
        if inv_file.exists():
            to_delete.append(inv_file)
            affected_locations.append(location)
    if not to_delete:
        raise FileNotFoundError(f"Inventory item '{sfid}' not found")
    # Compose commit message including both sfid tokens for all locations
    lines = [
        f"[smallfactory] Deleted inventory entries for {sfid} across {len(affected_locations)} location(s)",
        f"::sfid::{sfid}",
    ] + [f"::sfid::{loc}" for loc in sorted(set(affected_locations))]
    git_commit_paths(datarepo_path, to_delete, "\n".join(lines), delete=True)
    # Return minimal metadata
    name = ""
    ent_meta_path = _entity_file(datarepo_path, sfid)
    if ent_meta_path.exists():
        try:
            name = _read_yaml(ent_meta_path).get("name", "")
        except Exception:
            name = ""
    return {"sfid": sfid, "name": name, "deleted_locations": sorted(set(affected_locations))}


def adjust_quantity(datarepo_path: Path, sfid: str, delta: int, location: Optional[str] = None) -> dict:
    """Adjust quantity for an entity at a specific location per SPEC.

    If location is omitted and the entity exists at exactly one location, adjust there.
    Otherwise, require location.
    """
    # Verify entity exists
    if not _entity_exists(datarepo_path, sfid):
        raise FileNotFoundError(f"Entity sfid '{sfid}' does not exist under entities/")

    inventory_dir = ensure_inventory_dir(datarepo_path)
    candidate_files: List[Path] = []
    for loc_dir in sorted([p for p in inventory_dir.iterdir() if p.is_dir()]):
        loc_name = loc_dir.name
        if not loc_name.startswith("l_"):
            continue
        inv_file = loc_dir / f"{sfid}.yml"
        if inv_file.exists():
            candidate_files.append(inv_file)

    lf: Path
    if location is None:
        if len(candidate_files) == 1:
            lf = candidate_files[0]
            location = lf.parent.name
        elif len(candidate_files) == 0:
            raise ValueError("location is required because the item doesn't exist at any location yet")
        else:
            raise ValueError("location is required when an item exists at multiple locations")
    else:
        _validate_location_sfid(location)
        # Ensure location entity exists
        if not _entity_exists(datarepo_path, location):
            raise FileNotFoundError(f"Location sfid '{location}' does not exist under entities/")
        lf = _inventory_file(datarepo_path, location, sfid)
        # If location file doesn't exist yet, create it with starting quantity 0
        if not lf.exists():
            lf.parent.mkdir(parents=True, exist_ok=True)
            _write_yaml(lf, {"quantity": 0})

    data = _read_yaml(lf)
    try:
        new_qty = int(data.get("quantity", 0)) + int(delta)
    except Exception:
        raise ValueError("Could not adjust quantity")
    if new_qty < 0:
        raise ValueError("Resulting quantity cannot be negative")
    data["quantity"] = new_qty
    _write_yaml(lf, data)
    commit_msg = (
        f"[smallfactory] Adjusted quantity for {sfid} at {location} by {delta}\n"
        f"::sfid::{sfid}\n::sfid::{location}\n::sf-delta::{delta}\n::sf-new-quantity::{new_qty}"
    )
    git_commit_and_push(datarepo_path, lf, commit_msg)
    return view_item(datarepo_path, sfid)
