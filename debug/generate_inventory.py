#!/usr/bin/env python3
"""
smallFactory debug tool: generate synthetic inventory entries for stress testing.

Usage examples:
  python3 debug/generate_inventory.py 10000
  python3 debug/generate_inventory.py 10000 --seed 42 --min-locations 1 --max-locations 10
  python3 debug/generate_inventory.py 500 --datarepo ./datarepos/sf1

By default, the tool will try to resolve the datarepo using smallFactory's
config if available; otherwise, you can pass --datarepo explicitly.
"""
from __future__ import annotations

import argparse
import random
import re
from pathlib import Path
from typing import Optional, List, Set

import yaml

# Import light-weight helpers from core to locate datarepo and commit
try:
    from smallfactory.core.v1.config import get_datarepo_path, ensure_config
    from smallfactory.core.v1.gitutils import git_commit_paths
except Exception:
    # Fallbacks if core isn't importable in this environment. Git commits will be skipped.
    get_datarepo_path = None  # type: ignore
    ensure_config = None  # type: ignore
    git_commit_paths = None  # type: ignore


LOCATION_SFID_RE = re.compile(r"^l_[a-z0-9_]+$")


def validate_location_sfid(location_sfid: str) -> None:
    """Validate that a location identifier is a proper SFID with `l_` prefix.

    Examples: l_a1, l_rack_a12
    """
    if not location_sfid or location_sfid in {".", ".."}:
        raise ValueError("location_sfid must be a non-empty string")
    if "/" in location_sfid or "\\" in location_sfid:
        raise ValueError("location_sfid cannot contain path separators")
    if LOCATION_SFID_RE.fullmatch(location_sfid) is None:
        raise ValueError("location_sfid must match ^l_[a-z0-9_]+$")


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inv = datarepo_path / "inventory"
    inv.mkdir(parents=True, exist_ok=True)
    return inv


def ensure_entities_dir(datarepo_path: Path) -> Path:
    ents = datarepo_path / "entities"
    ents.mkdir(parents=True, exist_ok=True)
    return ents


def entity_meta_path(datarepo_path: Path, sfid: str) -> Path:
    # Canonical entity metadata path per SPEC v1
    # entities/<sfid>/entity.yml
    return ensure_entities_dir(datarepo_path) / sfid / "entity.yml"


def location_dir(datarepo_path: Path, location_sfid: str) -> Path:
    validate_location_sfid(location_sfid)
    return ensure_inventory_dir(datarepo_path) / location_sfid


def inventory_item_file(datarepo_path: Path, location_sfid: str, sfid: str) -> Path:
    """Path to inventory file for an entity at a location.

    inventory/<location_sfid>/<sfid>.yml
    """
    ldir = location_dir(datarepo_path, location_sfid)
    return ldir / f"{sfid}.yml"


def write_yaml(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def resolve_datarepo(explicit: Optional[str]) -> Path:
    if explicit:
        return Path(explicit).resolve()
    # Try by config if available
    if ensure_config and get_datarepo_path:
        try:
            ensure_config()
            return Path(get_datarepo_path()).resolve()
        except Exception:
            pass
    # Default fallback
    return (Path.cwd() / "datarepos" / "sf1").resolve()


def generate(
    datarepo_path: Path,
    count: int,
    *,
    id_prefix: str = "p_",
    name_prefix: str = "Part ",
    start_index: int = 1,
    min_locations: int = 1,
    max_locations: int = 10,
    min_qty: int = 0,
    max_qty: int = 100,
    seed: Optional[int] = None,
    batch_size: int = 500,
    no_git: bool = False,
    token_limit: int = 200,
) -> dict:
    if seed is not None:
        random.seed(seed)

    if count <= 0:
        return {"created": 0, "batches": 0}
    if min_locations < 1:
        min_locations = 1
    if max_locations < min_locations:
        max_locations = min_locations
    if min_qty < 0:
        min_qty = 0
    if max_qty < min_qty:
        max_qty = min_qty

    ensure_inventory_dir(datarepo_path)

    aisles = ["a", "b", "c", "d", "e", "f", "g", "h"]
    areas = ["zone", "rack", "bin", "row", "bay"]

    created = 0
    batches = 0
    paths_to_commit: List[Path] = []
    batch_item_sfids: Set[str] = set()
    batch_location_sfids: Set[str] = set()

    for i in range(start_index, start_index + count):
        sfid = f"{id_prefix}{i:05d}"
        pname = f"{name_prefix}{i:05d}"

        # Create/ensure canonical entity metadata for the item under entities/<sfid>/entity.yml
        item_entity_path = entity_meta_path(datarepo_path, sfid)
        if not item_entity_path.exists():
            # Do not persist 'sfid' in entity.yml; identity is directory name
            write_yaml(item_entity_path, {"name": pname})
            paths_to_commit.append(item_entity_path)
            # Track item token as well
            batch_item_sfids.add(sfid)

        nloc = random.randint(min_locations, max_locations)
        used = set()
        for _ in range(nloc):
            area = random.choice(areas)
            aisle = random.choice(aisles)
            num = random.randint(1, 40)
            # Build a simple l_ SFID like: l_zone_a12 or l_bin_h3
            loc_sfid = f"l_{area}_{aisle}{num}"
            # Normalize to lowercase and underscores only (already set above)
            loc_sfid = loc_sfid.lower().replace(" ", "_")
            if loc_sfid in used:
                loc_sfid = f"{loc_sfid}_{random.randint(1,99)}"
            validate_location_sfid(loc_sfid)
            used.add(loc_sfid)

            # Ensure a canonical entity file exists for the location SFID as well
            loc_entity_path = entity_meta_path(datarepo_path, loc_sfid)
            if not loc_entity_path.exists():
                # Do not persist 'sfid' in entity.yml; identity is directory name
                write_yaml(loc_entity_path, {"name": loc_sfid})
                paths_to_commit.append(loc_entity_path)
                batch_location_sfids.add(loc_sfid)

            # Write inventory entry for this item at this location
            qty = random.randint(min_qty, max_qty)
            inv_fp = inventory_item_file(datarepo_path, loc_sfid, sfid)
            write_yaml(inv_fp, {"quantity": qty})
            paths_to_commit.append(inv_fp)
            # Track tokens for commit messages per SPEC (both entity and location)
            batch_item_sfids.add(sfid)
            batch_location_sfids.add(loc_sfid)

        created += 1

        if not no_git and git_commit_paths and len(paths_to_commit) >= batch_size:
            token_lines: List[str] = []
            if batch_item_sfids:
                for eid in sorted(batch_item_sfids)[:token_limit]:
                    token_lines.append(f"::sfid::{eid}")
            if batch_location_sfids:
                for lid in sorted(batch_location_sfids)[:token_limit]:
                    token_lines.append(f"::sfid::{lid}")
            msg = (
                f"[smallFactory] Generated synthetic inventory batch (up to {created} items)\n"
                f"::sf-action::generate\n::sf-count::{created}"
            )
            if token_lines:
                msg = msg + "\n" + "\n".join(token_lines)
            git_commit_paths(datarepo_path, paths_to_commit, msg)
            batches += 1
            paths_to_commit = []
            batch_item_sfids.clear()
            batch_location_sfids.clear()

    if not no_git and git_commit_paths and paths_to_commit:
        token_lines: List[str] = []
        if batch_item_sfids:
            for eid in sorted(batch_item_sfids)[:token_limit]:
                token_lines.append(f"::sfid::{eid}")
        if batch_location_sfids:
            for lid in sorted(batch_location_sfids)[:token_limit]:
                token_lines.append(f"::sfid::{lid}")
        msg = (
            f"[smallFactory] Generated synthetic inventory final batch (total {created} items)\n"
            f"::sf-action::generate\n::sf-count::{created}"
        )
        if token_lines:
            msg = msg + "\n" + "\n".join(token_lines)
        git_commit_paths(datarepo_path, paths_to_commit, msg)
        batches += 1

    return {
        "created": created,
        "batches": batches,
        "datarepo": str(datarepo_path),
        "id_prefix": id_prefix,
        "start_index": start_index,
        "min_locations": min_locations,
        "max_locations": max_locations,
        "min_qty": min_qty,
        "max_qty": max_qty,
        "seed": seed,
        "git": not no_git and git_commit_paths is not None,
        "token_limit": token_limit,
    }


def main():
    p = argparse.ArgumentParser(description="Generate synthetic inventory for stress testing")
    p.add_argument("count", type=int, help="Number of items to generate")
    p.add_argument("--datarepo", help="Path to datarepo (defaults to config or ./datarepos/sf1)")
    p.add_argument("--id-prefix", default="p_", help="Prefix for generated SFIDs (default: p_)")
    p.add_argument("--name-prefix", default="Part ", help="Prefix for generated names (default: 'Part ')")
    p.add_argument("--start-index", type=int, default=1, help="Starting index for SFIDs (default: 1)")
    p.add_argument("--min-locations", type=int, default=1, help="Min locations per item (default: 1)")
    p.add_argument("--max-locations", type=int, default=10, help="Max locations per item (default: 10)")
    p.add_argument("--min-qty", type=int, default=0, help="Min quantity per location (default: 0)")
    p.add_argument("--max-qty", type=int, default=100, help="Max quantity per location (default: 100)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p.add_argument("--batch-size", type=int, default=500, help="Approx number of files per commit batch (default: 500)")
    p.add_argument("--no-git", action="store_true", help="Do not commit; just write files")
    p.add_argument("--token-limit", type=int, default=200, help="Max ::sfid:: tokens per type (entity/location) to include in each commit message")

    args = p.parse_args()

    datarepo = resolve_datarepo(args.datarepo)
    datarepo.mkdir(parents=True, exist_ok=True)

    summary = generate(
        datarepo,
        args.count,
        id_prefix=args.id_prefix,
        name_prefix=args.name_prefix,
        start_index=args.start_index,
        min_locations=args.min_locations,
        max_locations=args.max_locations,
        min_qty=args.min_qty,
        max_qty=args.max_qty,
        seed=args.seed,
        batch_size=args.batch_size,
        no_git=args.no_git,
        token_limit=args.token_limit,
    )

    print(yaml.safe_dump(summary, sort_keys=False))


if __name__ == "__main__":
    main()
