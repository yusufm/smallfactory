#!/usr/bin/env python3
"""
Smallfactory debug tool: generate synthetic inventory entries for stress testing.

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
from typing import Optional, List

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


ALLOWED_LOCATION_CHARS = re.compile(r"[A-Za-z0-9 ._-]+$")


def validate_location_name(location: str) -> None:
    if not location or location in {".", ".."}:
        raise ValueError("location must be a non-empty name")
    if "/" in location or "\\" in location:
        raise ValueError("location cannot contain path separators")
    if ALLOWED_LOCATION_CHARS.fullmatch(location) is None:
        raise ValueError("location contains invalid characters; allowed: letters, numbers, space, . _ -")


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inv = datarepo_path / "inventory"
    inv.mkdir(parents=True, exist_ok=True)
    return inv


def part_dir(datarepo_path: Path, pid: str) -> Path:
    return ensure_inventory_dir(datarepo_path) / pid


def part_meta_path(pdir: Path) -> Path:
    return pdir / "part.yml"


def location_file(pdir: Path, location: str) -> Path:
    validate_location_name(location)
    return pdir / f"{location}.yml"


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
    id_prefix: str = "part-",
    name_prefix: str = "Part ",
    start_index: int = 1,
    min_locations: int = 1,
    max_locations: int = 10,
    min_qty: int = 0,
    max_qty: int = 100,
    seed: Optional[int] = None,
    batch_size: int = 500,
    no_git: bool = False,
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

    aisles = ["A", "B", "C", "D", "E", "F", "G", "H"]
    areas = ["Shelf", "Bin", "Rack", "Drawer", "Pallet"]

    created = 0
    batches = 0
    paths_to_commit: List[Path] = []

    for i in range(start_index, start_index + count):
        pid = f"{id_prefix}{i:05d}"
        pname = f"{name_prefix}{i:05d}"

        pdir = part_dir(datarepo_path, pid)
        meta_path = part_meta_path(pdir)

        if not pdir.exists():
            pdir.mkdir(parents=True, exist_ok=True)

        write_yaml(meta_path, {"id": pid, "name": pname})
        paths_to_commit.append(meta_path)

        nloc = random.randint(min_locations, max_locations)
        used = set()
        for _ in range(nloc):
            area = random.choice(areas)
            aisle = random.choice(aisles)
            num = random.randint(1, 40)
            sep = "-" if area == "Rack" else " "
            locname = f"{area}{sep}{aisle}{num}"
            if locname in used:
                locname = f"{locname}-{random.randint(1,99)}"
            used.add(locname)

            qty = random.randint(min_qty, max_qty)
            lf = location_file(pdir, locname)
            write_yaml(lf, {"location": locname, "quantity": qty})
            paths_to_commit.append(lf)

        created += 1

        if not no_git and git_commit_paths and len(paths_to_commit) >= batch_size:
            msg = (
                f"[smallfactory] Generated synthetic inventory batch (up to {created} items)\n"
                f"::sf-action::generate\n::sf-count::{created}"
            )
            git_commit_paths(datarepo_path, paths_to_commit, msg)
            batches += 1
            paths_to_commit = []

    if not no_git and git_commit_paths and paths_to_commit:
        msg = (
            f"[smallfactory] Generated synthetic inventory final batch (total {created} items)\n"
            f"::sf-action::generate\n::sf-count::{created}"
        )
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
    }


def main():
    p = argparse.ArgumentParser(description="Generate synthetic inventory for stress testing")
    p.add_argument("count", type=int, help="Number of items to generate")
    p.add_argument("--datarepo", help="Path to datarepo (defaults to config or ./datarepos/sf1)")
    p.add_argument("--id-prefix", default="part-", help="Prefix for generated IDs (default: part-)")
    p.add_argument("--name-prefix", default="Part ", help="Prefix for generated names (default: 'Part ')")
    p.add_argument("--start-index", type=int, default=1, help="Starting index for IDs (default: 1)")
    p.add_argument("--min-locations", type=int, default=1, help="Min locations per item (default: 1)")
    p.add_argument("--max-locations", type=int, default=10, help="Max locations per item (default: 10)")
    p.add_argument("--min-qty", type=int, default=0, help="Min quantity per location (default: 0)")
    p.add_argument("--max-qty", type=int, default=100, help="Max quantity per location (default: 100)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p.add_argument("--batch-size", type=int, default=500, help="Approx number of files per commit batch (default: 500)")
    p.add_argument("--no-git", action="store_true", help="Do not commit; just write files")

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
    )

    print(yaml.safe_dump(summary, sort_keys=False))


if __name__ == "__main__":
    main()
