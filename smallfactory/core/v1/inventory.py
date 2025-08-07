from __future__ import annotations
from pathlib import Path
import yaml
import json

from .gitutils import git_commit_and_push


def ensure_inventory_dir(datarepo_path: Path) -> Path:
    inventory_dir = datarepo_path / "inventory"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    return inventory_dir


def add_item(datarepo_path: Path, item: dict) -> dict:
    inventory_dir = ensure_inventory_dir(datarepo_path)
    required = ["sku", "name", "quantity", "location"]
    missing = [f for f in required if f not in item]
    if missing:
        raise ValueError(f"Missing required field(s): {', '.join(missing)}")
    try:
        item["quantity"] = int(item["quantity"])
    except Exception:
        raise ValueError("quantity must be an integer")
    item_file = inventory_dir / f"{item['sku']}.yml"
    if item_file.exists():
        raise FileExistsError(f"Inventory item with SKU '{item['sku']}' already exists")
    with open(item_file, "w") as f:
        yaml.safe_dump(item, f)
    # Commit message
    commit_msg = [f"[smallfactory] Added inventory item {item['sku']} ({item['name']})", "::sf-action::add", f"::sf-sku::{item['sku']}"]
    for k, v in item.items():
        commit_msg.append(f"::sf-field::{k}={v}")
    git_commit_and_push(datarepo_path, item_file, "\n".join(commit_msg))
    return item


def list_items(datarepo_path: Path) -> list[dict]:
    inventory_dir = datarepo_path / "inventory"
    if not inventory_dir.exists():
        return []
    items: list[dict] = []
    for fpath in sorted(inventory_dir.glob("*.yml")):
        with open(fpath) as f:
            items.append(yaml.safe_load(f))
    return items


def view_item(datarepo_path: Path, sku: str) -> dict:
    inventory_dir = datarepo_path / "inventory"
    item_file = inventory_dir / f"{sku}.yml"
    if not item_file.exists():
        raise FileNotFoundError(f"Inventory item '{sku}' not found")
    with open(item_file) as f:
        return yaml.safe_load(f)


def update_item(datarepo_path: Path, sku: str, field: str, value: str) -> dict:
    inventory_dir = datarepo_path / "inventory"
    item_file = inventory_dir / f"{sku}.yml"
    if not item_file.exists():
        raise FileNotFoundError(f"Inventory item '{sku}' not found")
    with open(item_file) as f:
        item = yaml.safe_load(f)
    if field not in item:
        raise KeyError(f"Field '{field}' not in item")
    if field == "quantity":
        try:
            item[field] = int(value)
        except Exception:
            raise ValueError("Quantity must be an integer")
    else:
        item[field] = value
    with open(item_file, "w") as f:
        yaml.safe_dump(item, f)
    commit_msg = (
        f"[smallfactory] Updated {field} for inventory item {sku}\n"
        f"::sf-action::update\n::sf-sku::{sku}\n::sf-field::{field}\n::sf-value::{item[field]}"
    )
    git_commit_and_push(datarepo_path, item_file, commit_msg)
    return item


def delete_item(datarepo_path: Path, sku: str) -> dict:
    inventory_dir = datarepo_path / "inventory"
    item_file = inventory_dir / f"{sku}.yml"
    if not item_file.exists():
        raise FileNotFoundError(f"Inventory item '{sku}' not found")
    with open(item_file) as f:
        item = yaml.safe_load(f)
    item_file.unlink()
    commit_msg = (f"[smallfactory] Deleted inventory item {sku} ({item.get('name','')})\n" f"::sf-action::delete\n::sf-sku::{sku}")
    git_commit_and_push(datarepo_path, item_file, commit_msg)
    return item


def adjust_quantity(datarepo_path: Path, sku: str, delta: int) -> dict:
    inventory_dir = datarepo_path / "inventory"
    item_file = inventory_dir / f"{sku}.yml"
    if not item_file.exists():
        raise FileNotFoundError(f"Inventory item '{sku}' not found")
    with open(item_file) as f:
        item = yaml.safe_load(f)
    try:
        item["quantity"] = int(item.get("quantity", 0)) + int(delta)
    except Exception:
        raise ValueError("Could not adjust quantity")
    with open(item_file, "w") as f:
        yaml.safe_dump(item, f)
    commit_msg = (
        f"[smallfactory] Adjusted quantity for inventory item {sku} by {delta}\n"
        f"::sf-action::adjust\n::sf-sku::{sku}\n::sf-delta::{delta}\n::sf-new-quantity::{item['quantity']}"
    )
    git_commit_and_push(datarepo_path, item_file, commit_msg)
    return item
