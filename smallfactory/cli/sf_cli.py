import sys
import os
import argparse
import pathlib
import json
import yaml

from smallfactory import __version__
from smallfactory.core.v1.config import (
    ensure_config,
    get_datarepo_path,
    CONFIG_FILENAME,
    load_datarepo_config,
    INVENTORY_DEFAULT_FIELD_SPECS,
)
from smallfactory.core.v1 import repo as repo_ops
from smallfactory.core.v1.inventory import (
    add_item,
    list_items,
    view_item,
    delete_item,
    adjust_quantity,
)

# Entities core API
from smallfactory.core.v1.entities import (
    list_entities as ent_list_entities,
    get_entity as ent_get_entity,
    create_entity as ent_create_entity,
    update_entity_fields as ent_update_entity_fields,
    retire_entity as ent_retire_entity,
)


class SFArgumentParser(argparse.ArgumentParser):
    """ArgumentParser that prints full help on error instead of short usage."""
    def error(self, message):
        self.print_help()
        sys.stderr.write(f"\nError: {message}\n")
        raise SystemExit(2)


def main():
    # Root parser and global options (git-like)
    env_format = os.getenv("SF_FORMAT", "human").lower()
    if env_format not in ("human", "json", "yaml"):
        env_format = "human"
    parser = SFArgumentParser(prog="sf", description="smallFactory CLI")
    parser.add_argument("-R", "--repo", dest="repo", default=os.getenv("SF_REPO"), help="Override datarepo path")
    parser.add_argument(
        "-F", "--format", dest="format", choices=["human", "json", "yaml"], default=env_format,
        help="Output format (default from SF_FORMAT or 'human')"
    )
    parser.add_argument("-q", "--quiet", action="count", default=0, help="Decrease verbosity")
    parser.add_argument("-v", "--verbose", action="count", default=0, help="Increase verbosity")
    parser.add_argument("--version", action="version", version=f"smallFactory {__version__}")

    subparsers = parser.add_subparsers(dest="command", required=False, parser_class=SFArgumentParser)

    # init
    init_parser = subparsers.add_parser("init", help="Initialize a new datarepo at PATH")
    init_parser.add_argument("path", nargs="?", default=None, help="Target directory for new datarepo (optional)")

    # inventory group (nested subcommands)
    inventory_parser = subparsers.add_parser("inventory", aliases=["inv"], help="Inventory operations")
    inv_sub = inventory_parser.add_subparsers(dest="inv_cmd", required=False, parser_class=SFArgumentParser)

    inv_add = inv_sub.add_parser("add", help="Add a new inventory item")
    # Core required fields per SPEC/Core API
    inv_add.add_argument("--sfid", required=True, metavar="sfid", help="Entity SFID (e.g., p_m3x10)")
    inv_add.add_argument("--l_sfid", dest="location", required=True, metavar="l_sfid", help="Location SFID (e.g., l_a1)")
    inv_add.add_argument("--quantity", required=True, type=int, metavar="qty", help="On-hand quantity (>= 0)")
    # Additional optional metadata via key=value pairs
    inv_add.add_argument("--set", dest="set_pairs", action="append", default=[], help="Extra metadata key=value (repeatable)")

    inv_ls = inv_sub.add_parser("ls", aliases=["list"], help="List inventory items")

    inv_show = inv_sub.add_parser("show", aliases=["view"], help="Show an inventory item")
    inv_show.add_argument("sfid", help="Item SFID")

    # NOTE: Inventory no longer supports setting entity metadata; use entities module instead.

    inv_rm = inv_sub.add_parser("rm", aliases=["delete"], help="Remove an inventory item")
    inv_rm.add_argument("sfid", help="Item SFID")
    inv_rm.add_argument("-y", "--yes", action="store_true", help="Confirm deletion without prompt")

    inv_adjust = inv_sub.add_parser("adjust", help="Adjust quantity for an item at a location")
    inv_adjust.add_argument("location", metavar="l_sfid", help="Location SFID (e.g., l_a1)")
    inv_adjust.add_argument("sfid", metavar="sfid", help="Item SFID (e.g., p_m3x10)")
    inv_adjust.add_argument("delta", type=int, metavar="delta", help="Signed quantity delta (e.g. +5, -2)")

    # entities group (canonical metadata operations)
    entities_parser = subparsers.add_parser("entities", help="Entities operations")
    ent_sub = entities_parser.add_subparsers(dest="ent_cmd", required=False, parser_class=SFArgumentParser)

    ent_add = ent_sub.add_parser("add", help="Create a canonical entity")
    ent_add.add_argument("sfid", help="Entity SFID")
    ent_add.add_argument("pairs", nargs="*", help="key=value fields to set on creation")

    ent_ls = ent_sub.add_parser("ls", aliases=["list"], help="List entities")

    ent_show = ent_sub.add_parser("show", aliases=["view"], help="Show an entity")
    ent_show.add_argument("sfid", help="Entity SFID")

    ent_set = ent_sub.add_parser("set", help="Update fields for an entity")
    ent_set.add_argument("sfid", help="Entity SFID")
    ent_set.add_argument("pairs", nargs="+", help="key=value fields to set")

    ent_retire = ent_sub.add_parser("retire", help="Retire (soft-delete) an entity")
    ent_retire.add_argument("sfid", help="Entity SFID")
    ent_retire.add_argument("--reason", default=None, help="Retirement reason")

    # web command (kept top-level)
    web_parser = subparsers.add_parser("web", help="Start the web UI server")
    web_parser.add_argument("--port", type=int, default=8080, help="Port to run the web server on (default: 8080)")
    web_parser.add_argument("--host", default="0.0.0.0", help="Host to bind the web server to (default: 0.0.0.0)")
    web_parser.add_argument("--debug", action="store_true", help="Run in debug mode with auto-reload")

    # Pre-inject dynamic required flags for `inventory add` by inspecting argv
    ensure_config()
    argv = sys.argv[1:]
    positional_tokens = [t for t in argv if not t.startswith("-")]
    if len(positional_tokens) >= 2 and positional_tokens[0] in ("inventory", "inv") and positional_tokens[1] == "add":
        repo_override = None
        for i, a in enumerate(argv):
            if a in ("-R", "--repo") and i + 1 < len(argv):
                repo_override = argv[i + 1]
        # Try to read repo config if a repo override is provided; otherwise
        # attempt default repo, but fall back to defaults if not configured yet.
        dr_cfg = {}
        try:
            if repo_override:
                repo_path = pathlib.Path(repo_override).expanduser().resolve()
                dr_cfg = load_datarepo_config(repo_path)
            else:
                # This may raise if no default repo exists yet.
                dr_cfg = load_datarepo_config(None)
        except Exception:
            dr_cfg = {}
        fields_cfg = (dr_cfg.get("inventory", {}) or {}).get("fields") or INVENTORY_DEFAULT_FIELD_SPECS
        for fname, meta in fields_cfg.items():
            if meta.get("required"):
                # Map field name to canonical CLI option
                if fname == "location":
                    opt = "--l_sfid"
                    kwargs = {"required": True, "help": meta.get("description", ""), "dest": "location", "metavar": "l_sfid"}
                else:
                    opt = f"--{fname}"
                    kwargs = {"required": True, "help": meta.get("description", "")}
                    if fname == "quantity":
                        kwargs["type"] = int
                # Skip if option already exists (by option string or destination)
                existing_opts = [s for a in inv_add._actions for s in (a.option_strings or [])]
                existing_dests = [getattr(a, "dest", None) for a in inv_add._actions]
                desired_dest = kwargs.get("dest", fname.replace('-', '_'))
                if (opt in existing_opts) or (desired_dest in existing_dests):
                    continue
                inv_add.add_argument(opt, **kwargs)

    args, unknown = parser.parse_known_args()

    # If there are unknown tokens, print the most relevant full help and exit with error
    if unknown:
        cmd = getattr(args, "command", None)
        if cmd in ("inventory", "inv"):
            inventory_parser.print_help()
        elif cmd == "entities":
            entities_parser.print_help()
        else:
            parser.print_help()
        sys.exit(2)

    # Helper: resolve repo path honoring -R/--repo
    def _repo_path() -> pathlib.Path:
        if getattr(args, "repo", None):
            return pathlib.Path(args.repo).expanduser().resolve()
        return get_datarepo_path()

    # Helper: normalize format
    def _fmt() -> str:
        return args.format

    def cmd_init(args):
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
                    print("[smallFactory] Error: datarepo name cannot be empty.")
                    sys.exit(1)
            target_path = datarepos_dir / repo_name

        if target_path.exists() and os.listdir(str(target_path)):
            print(f"[smallFactory] Error: Target directory '{target_path}' already exists and is not empty.")
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
        repo_ops.initial_commit_and_optional_push(repo_path, has_remote)

    def cmd_inventory_add(args):
        datarepo_path = _repo_path()
        # Load field specs for any additional required fields beyond the core ones
        dr_cfg = load_datarepo_config(datarepo_path)
        fields_cfg = (dr_cfg.get("inventory", {}) or {}).get("fields") or INVENTORY_DEFAULT_FIELD_SPECS
        required_fields = [fname for fname, meta in fields_cfg.items() if meta.get("required")]
        # Build base item from core required args
        item = {
            "sfid": args.sfid,
            "location": args.location,
            "quantity": args.quantity,
        }
        # Include any additional repo-required fields (if present)
        for fname in required_fields:
            if fname in ("sfid", "location", "quantity"):
                continue
            val = getattr(args, fname.replace('-', '_'), None)
            if val is None:
                print(f"[smallFactory] Error: missing required field '--{fname}'")
                sys.exit(2)
            item[fname] = val
        # Parse extra --set key=value pairs
        if args.set_pairs:
            for pair in args.set_pairs:
                if "=" not in pair:
                    print(f"[smallFactory] Error: invalid --set pair '{pair}', expected key=value")
                    sys.exit(1)
                k, v = pair.split("=", 1)
                item[k.strip()] = v.strip()
        try:
            added = add_item(datarepo_path, item)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        # Output
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(added, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(added, sort_keys=False))
        else:
            print(f"[smallFactory] Added inventory item '{added['sfid']}' to datarepo at {datarepo_path}")

    def cmd_inventory_list(args):
        datarepo_path = _repo_path()
        items = list_items(datarepo_path)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(items, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(items, sort_keys=False))
        else:
            if not items:
                print("[smallFactory] No inventory items found.")
                sys.exit(0)
            # Dynamically determine all fields
            required = ["sfid", "name", "quantity", "location"]
            extra_fields = set()
            for item in items:
                extra_fields.update(item.keys())
            # Do not show the raw 'locations' list column in human view
            extra_fields = [f for f in sorted(extra_fields) if f not in required + ["locations"]]
            fields = required + extra_fields
            # Print header
            header = " | ".join(f"{f.title():<15}" for f in fields)
            print(header)
            print("-" * len(header))
            # Print rows
            for item in items:
                # First, print the main summary row
                row = []
                for f in fields:
                    val = item.get(f, "")
                    if f == "name":
                        val = str(val)[:20]
                    row.append(f"{str(val):<15}")
                print(" | ".join(row))

                # Then, if multiple locations exist, print sub-lines for each location with its quantity
                locs = item.get("locations", [])
                if isinstance(locs, list) and len(locs) > 1:
                    try:
                        details = view_item(datarepo_path, item.get("sfid", ""))
                        loc_map = details.get("locations", {})
                    except Exception:
                        loc_map = {}
                    for loc_name in sorted(loc_map.keys()):
                        qty = loc_map.get(loc_name, "")
                        sub_row = []
                        for f in fields:
                            if f == "sfid" or f == "name":
                                val = ""
                            elif f == "quantity":
                                val = qty
                            elif f == "location":
                                val = loc_name
                            else:
                                val = ""
                            sub_row.append(f"{str(val):<15}")
                        print(" | ".join(sub_row))

    def cmd_inventory_view(args):
        datarepo_path = _repo_path()
        try:
            item = view_item(datarepo_path, args.sfid)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(item, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(yaml.safe_dump(item, sort_keys=False))

    # Removed: cmd_inventory_update; inventory does not edit entity metadata per SPEC.

    def cmd_inventory_delete(args):
        datarepo_path = _repo_path()
        fmt = _fmt()
        if fmt == "human" and not getattr(args, "yes", False) and sys.stdout.isatty():
            confirm = input(f"Are you sure you want to delete inventory item '{args.sfid}'? [y/N]: ").strip().lower()
            if confirm not in ("y", "yes"):
                print("[smallFactory] Delete cancelled.")
                sys.exit(0)
        try:
            item = delete_item(datarepo_path, args.sfid)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        # Output
        if fmt == "json":
            print(json.dumps(item, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(f"[smallFactory] Deleted inventory item '{args.sfid}' from datarepo at {datarepo_path}")

    def cmd_inventory_adjust(args):
        datarepo_path = _repo_path()
        try:
            item = adjust_quantity(datarepo_path, args.sfid, args.delta, location=args.location)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        # Output
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(item, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(item, sort_keys=False))
        else:
            print(f"[smallFactory] Adjusted quantity for inventory item '{args.sfid}' at '{args.location}' by {args.delta} in datarepo at {datarepo_path}")

    # Entities command handlers
    def _parse_pairs(pairs_list):
        updates = {}
        for pair in pairs_list or []:
            if "=" not in pair:
                print(f"[smallFactory] Error: invalid key=value pair '{pair}'")
                sys.exit(1)
            k, v = pair.split("=", 1)
            updates[k.strip()] = v.strip()
        return updates

    def cmd_entities_add(args):
        datarepo_path = _repo_path()
        fields = _parse_pairs(args.pairs)
        try:
            ent = ent_create_entity(datarepo_path, args.sfid, fields or None)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            print(f"[smallFactory] Created entity '{args.sfid}' in datarepo at {datarepo_path}")

    def cmd_entities_list(args):
        datarepo_path = _repo_path()
        ents = ent_list_entities(datarepo_path)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ents, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ents, sort_keys=False))
        else:
            if not ents:
                print("[smallFactory] No entities found.")
                return
            fields = ["sfid", "name", "retired"]
            header = " | ".join(f"{f.title():<15}" for f in fields)
            print(header)
            print("-" * len(header))
            for e in ents:
                row = [f"{str(e.get(f, '')):<15}" for f in fields]
                print(" | ".join(row))

    def cmd_entities_show(args):
        datarepo_path = _repo_path()
        try:
            ent = ent_get_entity(datarepo_path, args.sfid)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            print(yaml.safe_dump(ent, sort_keys=False))

    def cmd_entities_set(args):
        datarepo_path = _repo_path()
        updates = _parse_pairs(args.pairs)
        if not updates:
            print("[smallFactory] Error: no key=value pairs provided")
            sys.exit(2)
        try:
            ent = ent_update_entity_fields(datarepo_path, args.sfid, updates)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            changed = ", ".join(sorted(updates.keys()))
            print(f"[smallFactory] Updated entity '{args.sfid}' fields: {changed}")

    def cmd_entities_retire(args):
        datarepo_path = _repo_path()
        try:
            ent = ent_retire_entity(datarepo_path, args.sfid, reason=getattr(args, "reason", None))
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            print(f"[smallFactory] Retired entity '{args.sfid}'")

    def cmd_web(args):
        try:
            # Import Flask app here to avoid import issues if Flask isn't installed
            import sys
            from pathlib import Path
            
            # Add the project root to Python path for web imports
            project_root = Path(__file__).parent.parent.parent
            sys.path.insert(0, str(project_root))
            
            from web.app import app
            
            print("🏭 Starting smallFactory Web UI...")
            print(f"📍 Access the interface at: http://localhost:{args.port}")
            print("🔧 Git-native PLM for 1-2 person teams")
            print("=" * 50)
            
            try:
                app.run(
                    debug=args.debug,
                    host=args.host,
                    port=args.port,
                    use_reloader=args.debug
                )
            except KeyboardInterrupt:
                print("\n👋 Shutting down smallFactory Web UI...")
            except Exception as e:
                if "Address already in use" in str(e):
                    print(f"❌ Error: Port {args.port} is already in use.")
                    print(f"   Try using a different port: python sf.py web --port {args.port + 1}")
                else:
                    print(f"❌ Error starting web server: {e}")
                sys.exit(1)
                
        except ImportError as e:
            print("❌ Error: Flask is not installed.")
            print("   Install web dependencies: pip install -r web/requirements.txt")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error starting web UI: {e}")
            sys.exit(1)

    # Dispatch via table with alias normalization
    cmd = args.command
    if cmd == "inv":
        cmd = "inventory"

    # Determine subcommand for the current group
    if cmd == "inventory":
        sub = getattr(args, "inv_cmd", None)
    elif cmd == "entities":
        sub = getattr(args, "ent_cmd", None)
    else:
        sub = None
    if sub in ("ls", "list"):
        sub = "ls"
    elif sub in ("show", "view"):
        sub = "show"
    elif sub in ("rm", "delete"):
        sub = "rm"

    DISPATCH = {
        ("init", None): cmd_init,
        ("web", None): cmd_web,
        ("inventory", "add"): cmd_inventory_add,
        ("inventory", "ls"): cmd_inventory_list,
        ("inventory", "show"): cmd_inventory_view,
        ("inventory", "rm"): cmd_inventory_delete,
        ("inventory", "adjust"): cmd_inventory_adjust,
        ("entities", "add"): cmd_entities_add,
        ("entities", "ls"): cmd_entities_list,
        ("entities", "show"): cmd_entities_show,
        ("entities", "set"): cmd_entities_set,
        ("entities", "retire"): cmd_entities_retire,
    }

    handler = DISPATCH.get((cmd, sub))
    if handler:
        handler(args)
    else:
        if cmd == "inventory":
            inventory_parser.print_help()
        else:
            parser.print_help()

