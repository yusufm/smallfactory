from __future__ import annotations
from pathlib import Path
import yaml
import json
import re
from typing import Optional, List, Dict

from .gitutils import git_commit_and_push, git_commit_paths


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inventory_dir = datarepo_path / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    return inventory_dir


# -------------------------------
# Helpers for new storage layout
# inventory/{id}/part.yml
# inventory/{id}/{location}.yml
# -------------------------------

def _part_dir(datarepo_path: Path, id: str) -> Path:
    return ensure_inventory_dir(datarepo_path) / id


def _part_meta_path(part_dir: Path) -> Path:
    return part_dir / "part.yml"


def _validate_location_name(location: str) -> None:
    """Ensure location is a safe file name without slugifying.

    Allowed: A-Z a-z 0-9 . _ - (no path separators)."""
    if not location or location in {".", ".."}:
        raise ValueError("location must be a non-empty name")
    if "/" in location or "\\" in location:
        raise ValueError("location cannot contain path separators")
    if not re.fullmatch(r"[A-Za-z0-9 ._-]+", location):
        raise ValueError("location contains invalid characters; allowed: letters, numbers, space, . _ -")


def _location_file(part_dir: Path, location: str) -> Path:
    _validate_location_name(location)
    return part_dir / f"{location}.yml"


def _read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _write_yaml(p: Path, data: dict) -> None:
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def add_item(datarepo_path: Path, item: dict) -> dict:
    # Required fields for creation (accept legacy 'sku' as alias for 'id')
    if "id" not in item and "sku" in item:
        # normalize incoming alias
        item = {**item, "id": item.get("id") or item.get("sku")}
    required = ["id", "name", "quantity", "location"]
    missing = [f for f in required if f not in item]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")
    # Parse and validate
    id = str(item["id"]).strip()
    name = str(item["name"]).strip()
    location = str(item["location"]).strip()
    try:
        quantity = int(item["quantity"])
    except Exception:
        raise ValueError("quantity must be an integer")
    if quantity < 0:
        raise ValueError("quantity cannot be negative")
    _validate_location_name(location)

    # Prepare paths
    pdir = _part_dir(datarepo_path, id)
    meta_path = _part_meta_path(pdir)
    if meta_path.exists():
        # Part already exists: add a new location for this ID
        meta = _read_yaml(meta_path)
        loc_path = _location_file(pdir, location)
        if loc_path.exists():
            raise FileExistsError(
                f"Location '{location}' already exists for ID '{id}'. Use inventory-adjust to modify its quantity."
            )
        # Optionally merge any extra metadata fields provided
        extras = {k: v for k, v in item.items() if k not in {"id", "sku", "name", "quantity", "location"}}
        paths_to_commit = []
        if extras:
            meta.update(extras)
            _write_yaml(meta_path, meta)
            paths_to_commit.append(meta_path)
        # Write the new location file
        _write_yaml(loc_path, {"location": location, "quantity": quantity})
        paths_to_commit.append(loc_path)
        commit_lines = [
            f"[smallfactory] Added location '{location}' for inventory item {id} with quantity {quantity}",
            "::sf-action::add-location",
            f"::sf-id::{id}",
            f"::sf-location::{location}",
            f"::sf-quantity::{quantity}",
        ]
        git_commit_paths(datarepo_path, paths_to_commit, "\n".join(commit_lines))
        # Return combined view
        return view_item(datarepo_path, id)
    else:
        # New part: create directory and initial location
        pdir.mkdir(parents=True, exist_ok=True)
        loc_path = _location_file(pdir, location)

        # Write files
        meta = {"id": id, "name": name}
        # include any extra fields except quantity/location
        for k, v in item.items():
            if k not in {"id", "sku", "name", "quantity", "location"}:
                meta[k] = v
        _write_yaml(meta_path, meta)
        _write_yaml(loc_path, {"location": location, "quantity": quantity})

        # Commit both files together
        commit_lines = [
            f"[smallfactory] Added inventory item {id} ({name})",
            "::sf-action::add",
            f"::sf-id::{id}",
            f"::sf-field::name={name}",
            f"::sf-field::location={location}",
            f"::sf-field::quantity={quantity}",
        ]
        git_commit_paths(datarepo_path, [meta_path, loc_path], "\n".join(commit_lines))
        # Return a flattened view (for CLI compatibility)
        result = {**meta, "quantity": quantity, "location": location}
        return result


def list_items(datarepo_path: Path) -> list[dict]:
    inventory_dir = ensure_inventory_dir(datarepo_path)
    items: List[Dict] = []
    for pdir in sorted([p for p in inventory_dir.iterdir() if p.is_dir()]):
        meta_path = _part_meta_path(pdir)
        if not meta_path.exists():
            continue
        meta = _read_yaml(meta_path)
        # Aggregate quantity across all location files (any *.yml except part.yml)
        total_qty = 0
        locations: List[str] = []
        for lf in sorted(pdir.glob("*.yml")):
            if lf.name == "part.yml":
                continue
            data = _read_yaml(lf)
            qty = int(data.get("quantity", 0))
            total_qty += qty
            locname = lf.stem
            locations.append(locname)
        # derive id with backward-compatibility
        _id = meta.get("id") or meta.get("sku") or pdir.name
        items.append({
            "id": _id,
            "name": meta.get("name"),
            "quantity": total_qty,
            # For human output, show multiple or single location name
            "location": locations[0] if len(locations) == 1 else ("multiple" if locations else ""),
            "locations": locations,
            # do not surface legacy 'sku' in extras
            **{k: v for k, v in meta.items() if k not in {"id", "sku", "name"}},
        })
    return items


def view_item(datarepo_path: Path, id: str) -> dict:
    pdir = _part_dir(datarepo_path, id)
    meta_path = _part_meta_path(pdir)
    if not meta_path.exists():
        raise FileNotFoundError(f"Inventory item '{id}' not found")
    meta = _read_yaml(meta_path)
    # Normalize output to always include 'id'
    out_id = meta.get("id") or meta.get("sku") or id
    locations: Dict[str, int] = {}
    total_qty = 0
    for lf in sorted(pdir.glob("*.yml")):
        if lf.name == "part.yml":
            continue
        data = _read_yaml(lf)
        qty = int(data.get("quantity", 0))
        locname = lf.stem
        locations[locname] = qty
        total_qty += qty
    # Exclude legacy 'sku' from top-level output to present 'id' everywhere
    base = {k: v for k, v in meta.items() if k != "sku"}
    return {**base, "id": out_id, "quantity": total_qty, "locations": locations}


def update_item(datarepo_path: Path, id: str, field: str, value: str) -> dict:
    # Update metadata in part.yml only (not quantities or locations here)
    pdir = _part_dir(datarepo_path, id)
    meta_path = _part_meta_path(pdir)
    if not meta_path.exists():
        raise FileNotFoundError(f"Inventory item '{id}' not found")
    item = _read_yaml(meta_path)
    if field in {"quantity", "location", "locations"}:
        raise ValueError("Use inventory-adjust to change quantities; location files are managed per-location")
    item[field] = value
    _write_yaml(meta_path, item)
    commit_msg = (
        f"[smallfactory] Updated {field} for inventory item {id}\n"
        f"::sf-action::update\n::sf-id::{id}\n::sf-field::{field}\n::sf-value::{item[field]}"
    )
    git_commit_and_push(datarepo_path, meta_path, commit_msg)
    return item


def delete_item(datarepo_path: Path, id: str) -> dict:
    pdir = _part_dir(datarepo_path, id)
    meta_path = _part_meta_path(pdir)
    if not meta_path.exists():
        raise FileNotFoundError(f"Inventory item '{id}' not found")
    meta = _read_yaml(meta_path)
    # Collect all files to remove
    files = [fp for fp in pdir.glob("*.yml")]  # includes part.yml and all locations
    commit_msg = (
        f"[smallfactory] Deleted inventory item {id} ({meta.get('name','')})\n::sf-action::delete\n::sf-id::{id}"
    )
    # Stage deletions via git rm and commit
    git_commit_paths(datarepo_path, files, commit_msg, delete=True)
    # Remove directory if empty (ignore errors if not)
    try:
        pdir.rmdir()
    except Exception:
        pass
    return meta


def adjust_quantity(datarepo_path: Path, id: str, delta: int, location: Optional[str] = None) -> dict:
    """Adjust quantity for a specific location. If location is not provided and
    the part has a single location, adjust that one. Otherwise, require location."""
    pdir = _part_dir(datarepo_path, id)
    meta_path = _part_meta_path(pdir)
    if not meta_path.exists():
        raise FileNotFoundError(f"Inventory item '{id}' not found")
    meta = _read_yaml(meta_path)

    loc_files = [lf for lf in pdir.glob("*.yml") if lf.name != "part.yml"]
    if location is None:
        if len(loc_files) == 1:
            lf = loc_files[0]
            location = lf.stem
        else:
            raise ValueError("location is required when a part has multiple locations")
    else:
        _validate_location_name(location)
        lf = _location_file(pdir, location)

    # If location file doesn't exist, create it with starting quantity 0
    if not lf.exists():
        _write_yaml(lf, {"location": location, "quantity": 0})

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
        f"[smallfactory] Adjusted quantity for inventory item {id} at {location} by {delta}\n"
        f"::sf-action::adjust\n::sf-id::{id}\n::sf-location::{location}\n::sf-delta::{delta}\n::sf-new-quantity::{new_qty}"
    )
    git_commit_and_push(datarepo_path, lf, commit_msg)
    # Return combined view for convenience
    out = view_item(datarepo_path, id)
    return out
