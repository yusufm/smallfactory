import sys
import os
import argparse
import pathlib
import json
import yaml

from smallfactory.core.v1.config import ensure_config, get_datarepo_path, CONFIG_FILENAME
from smallfactory.core.v1 import repo as repo_ops
from smallfactory.core.v1.inventory import (
    ensure_inventory_dir,
    add_item,
    list_items,
    view_item,
    update_item,
    delete_item,
    adjust_quantity,
)


def main():
    parser = argparse.ArgumentParser(description="smallfactory CLI")
    subparsers = parser.add_subparsers(dest="command")

    # create command
    create_parser = subparsers.add_parser("create", help="Create a new datarepo at the given path")
    create_parser.add_argument("path", nargs="?", default=None, help="Target directory for new datarepo (optional)")

    # inventory-add
    add_parser = subparsers.add_parser("inventory-add", help="Add a new inventory item")
    add_parser.add_argument(
        "fields", nargs='+',
        help="Inventory item fields as key=value pairs. Required: sku, name, quantity, location. Example: sku=001 name=Widget quantity=5 location='Aisle 2' color=red"
    )
    add_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    # inventory-list
    list_parser = subparsers.add_parser("inventory-list", help="List all inventory items")
    list_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    # inventory-view
    view_parser = subparsers.add_parser("inventory-view", help="View details of an inventory item")
    view_parser.add_argument("sku", help="SKU of the item to view")
    view_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    # inventory-update
    update_parser = subparsers.add_parser("inventory-update", help="Update an inventory item")
    update_parser.add_argument("sku", help="SKU of the item to update")
    update_parser.add_argument("field", help="Field to update (name, quantity, location)")
    update_parser.add_argument("value", help="New value for the field")
    update_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    # inventory-delete
    delete_parser = subparsers.add_parser("inventory-delete", help="Delete an inventory item")
    delete_parser.add_argument("sku", help="SKU of the item to delete")
    delete_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    # inventory-adjust
    adjust_parser = subparsers.add_parser("inventory-adjust", help="Adjust the stock level of an inventory item")
    adjust_parser.add_argument("sku", help="SKU of the item to adjust")
    adjust_parser.add_argument("delta", type=int, help="Amount to adjust (positive or negative)")
    adjust_parser.add_argument("-o", "--output", choices=["human", "json", "yaml"], default="human", help="Output format")

    args = parser.parse_args()

    ensure_config()

    def cmd_create(args):
        github_url = input("Paste the GitHub repository URL to clone/use (or leave blank for a new local-only repo): ").strip()

        if args.path:
            target_path = pathlib.Path(args.path)
        else:
            datarepos_dir = pathlib.Path('datarepos')
            datarepos_dir.mkdir(exist_ok=True)
            if github_url:
                # derive name from URL
                repo_name = github_url.split('/')[-1].replace('.git', '')
            else:
                # if no URL, ask for a name
                repo_name = input("Enter a name for the new local datarepo: ").strip()
                if not repo_name:
                    print("[smallfactory] Error: datarepo name cannot be empty.")
                    sys.exit(1)
            target_path = datarepos_dir / repo_name

        if target_path.exists() and os.listdir(str(target_path)):
            print(f"[smallfactory] Error: Target directory '{target_path}' already exists and is not empty.")
            sys.exit(1)

        repo_path = repo_ops.create_or_clone(target_path, github_url or None)

        # If cloned, skip remote setup (already present). If new, optionally prompt to add remote.
        has_remote = bool(github_url)
        if not has_remote:
            add_remote = input("Would you like to add a GitHub remote now? [y/N]: ").strip().lower()
            if add_remote in ("y", "yes"):
                remote_url = input("Paste the GitHub repository URL here: ").strip()
                if remote_url:
                    repo_ops.set_remote(repo_path, remote_url)
                    has_remote = True

        repo_ops.write_datarepo_config(repo_path)
        repo_ops.set_default_datarepo(repo_path)
        ensure_inventory_dir(repo_path)
        repo_ops.initial_commit_and_optional_push(repo_path, has_remote)

    def cmd_inventory_add(args):
        datarepo_path = get_datarepo_path()
        # Parse all fields from key=value pairs
        item = {}
        invalid_pairs = []
        for pair in args.fields:
            if '=' in pair:
                key, value = pair.split('=', 1)
                item[key.strip()] = value.strip()
            else:
                invalid_pairs.append(pair)
        if invalid_pairs:
            print("[smallfactory] Error: All fields must be in key=value format.")
            print(f"Invalid field(s): {', '.join(invalid_pairs)}")
            print("Usage: sf inventory-add sku=12345 name=test_item quantity=10 location=warehouse_a [other=val ...]")
            sys.exit(1)
        try:
            added = add_item(datarepo_path, item)
        except Exception as e:
            print(f"[smallfactory] Error: {e}")
            sys.exit(1)
        # Output
        if args.output == "json":
            print(json.dumps(added, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(added, sort_keys=False))
        else:
            print(f"[smallfactory] Added inventory item '{added['sku']}' to datarepo at {datarepo_path}")

    def cmd_inventory_list(args):
        datarepo_path = get_datarepo_path()
        items = list_items(datarepo_path)
        if args.output == "json":
            print(json.dumps(items, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(items, sort_keys=False))
        else:
            if not items:
                print("[smallfactory] No inventory items found.")
                sys.exit(0)
            # Dynamically determine all fields
            required = ["sku", "name", "quantity", "location"]
            extra_fields = set()
            for item in items:
                extra_fields.update(item.keys())
            extra_fields = [f for f in sorted(extra_fields) if f not in required]
            fields = required + extra_fields
            # Print header
            header = " | ".join(f"{f.title():<15}" for f in fields)
            print(header)
            print("-" * len(header))
            # Print rows
            for item in items:
                row = []
                for f in fields:
                    val = item.get(f, "")
                    if f == "name":
                        val = str(val)[:20]
                    row.append(f"{str(val):<15}")
                print(" | ".join(row))

    def cmd_inventory_view(args):
        datarepo_path = get_datarepo_path()
        try:
            item = view_item(datarepo_path, args.sku)
        except Exception as e:
            print(f"[smallfactory] Error: {e}")
            sys.exit(1)
        if args.output == "json":
            print(json.dumps(item, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(yaml.safe_dump(item, sort_keys=False))

    def cmd_inventory_update(args):
        datarepo_path = get_datarepo_path()
        try:
            item = update_item(datarepo_path, args.sku, args.field, args.value)
        except Exception as e:
            print(f"[smallfactory] Error: {e}")
            sys.exit(1)
        # Output
        if args.output == "json":
            print(json.dumps(item, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(f"[smallfactory] Updated '{args.field}' for inventory item '{args.sku}' in datarepo at {datarepo_path}")

    def cmd_inventory_delete(args):
        datarepo_path = get_datarepo_path()
        if args.output == "human":
            confirm = input(f"Are you sure you want to delete inventory item '{args.sku}'? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("[smallfactory] Delete cancelled.")
                sys.exit(0)
        try:
            item = delete_item(datarepo_path, args.sku)
        except Exception as e:
            print(f"[smallfactory] Error: {e}")
            sys.exit(1)
        # Output
        if args.output == "json":
            print(json.dumps(item, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(f"[smallfactory] Deleted inventory item '{args.sku}' from datarepo at {datarepo_path}")

    def cmd_inventory_adjust(args):
        datarepo_path = get_datarepo_path()
        try:
            item = adjust_quantity(datarepo_path, args.sku, args.delta)
        except Exception as e:
            print(f"[smallfactory] Error: {e}")
            sys.exit(1)
        # Output
        if args.output == "json":
            print(json.dumps(item, indent=2))
        elif args.output == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(f"[smallfactory] Adjusted quantity for inventory item '{args.sku}' by {args.delta} in datarepo at {datarepo_path}")

    COMMANDS = {
        "create": cmd_create,
        "inventory-add": cmd_inventory_add,
        "inventory-list": cmd_inventory_list,
        "inventory-view": cmd_inventory_view,
        "inventory-update": cmd_inventory_update,
        "inventory-delete": cmd_inventory_delete,
        "inventory-adjust": cmd_inventory_adjust,
    }

    if args.command in COMMANDS:
        COMMANDS[args.command](args)
    else:
        parser.print_help()
