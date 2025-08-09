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

# Stickers generation (QR only)
from smallfactory.core.v1.stickers import (
    generate_sticker_for_entity as st_generate_sticker_for_entity,
    check_dependencies as st_check_dependencies,
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
    entities_parser = subparsers.add_parser("entities", help="Entities (canonical metadata) operations")
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

    # stickers group (generate codes for entities)
    stickers_parser = subparsers.add_parser("stickers", help="Sticker generation for entities (PDF batch by default)")
    st_sub = stickers_parser.add_subparsers(dest="st_cmd", required=False, parser_class=SFArgumentParser)

    # Allow using `sf stickers` directly with batch options
    stickers_parser.add_argument(
        "--sfids",
        dest="sfids",
        default=None,
        help="Comma or newline separated SFIDs. Use '-' to read from stdin",
    )
    stickers_parser.add_argument(
        "--file",
        dest="file",
        default=None,
        help="Path to a file containing SFIDs (one per line or comma-separated)",
    )
    stickers_parser.add_argument(
        "--fields",
        dest="fields",
        default=None,
        help="Comma-separated list of additional fields to print as text (besides name/SFID)",
    )
    stickers_parser.add_argument(
        "--size",
        dest="size",
        default="2x1",
        help="Sticker size in inches, WIDTHxHEIGHT (default 2x1)",
    )
    stickers_parser.add_argument("--dpi", dest="dpi", type=int, default=300, help="Dots per inch for rendering (default 300)")
    stickers_parser.add_argument(
        "-o",
        "--out",
        dest="out",
        default="stickers.pdf",
        help="Output PDF filename (default: stickers.pdf)",
    )

    # NOTE: 'batch' is the default stickers interface and can handle a single SFID too.

    # stickers batch: generate multi-page PDF with one sticker per page
    st_batch = st_sub.add_parser("batch", help="Generate a multi-page PDF of stickers (one per page)")
    st_batch.add_argument(
        "--sfids",
        dest="sfids",
        default=None,
        help="Comma or newline separated SFIDs. Use '-' to read from stdin",
    )
    st_batch.add_argument(
        "--file",
        dest="file",
        default=None,
        help="Path to a file containing SFIDs (one per line or comma-separated)",
    )
    st_batch.add_argument(
        "--fields",
        dest="fields",
        default=None,
        help="Comma-separated list of additional fields to print as text (besides name/SFID)",
    )
    st_batch.add_argument(
        "--size",
        dest="size",
        default="2x1",
        help="Sticker size in inches, WIDTHxHEIGHT (default 2x1)",
    )
    st_batch.add_argument("--dpi", dest="dpi", type=int, default=300, help="Dots per inch for rendering (default 300)")
    st_batch.add_argument(
        "-o",
        "--out",
        dest="out",
        default="stickers.pdf",
        help="Output PDF filename (default: stickers.pdf)",
    )

    def _parse_size(sz: str, dpi: int):
        if not sz:
            return (600, 300)  # 2x1 inches @ 300 DPI
        try:
            st = sz.lower().replace("in", "").strip()
            w_s, h_s = st.split("x", 1)
            w_in, h_in = float(w_s), float(h_s)
            if w_in <= 0 or h_in <= 0 or dpi <= 0:
                raise ValueError
            return (int(round(w_in * dpi)), int(round(h_in * dpi)))
        except Exception:
            raise SystemExit("Invalid --size/--dpi. Use WIDTHxHEIGHT inches (e.g., 2x1) and positive DPI (e.g., 300)")

    def _parse_size_inches(sz: str):
        if not sz:
            return (2.0, 1.0)
        try:
            st = sz.lower().replace("in", "").strip()
            w_s, h_s = st.split("x", 1)
            w_in, h_in = float(w_s), float(h_s)
            if w_in <= 0 or h_in <= 0:
                raise ValueError
            return (w_in, h_in)
        except Exception:
            raise SystemExit("Invalid --size. Use WIDTHxHEIGHT inches (e.g., 2x1)")

    # Removed: cmd_stickers_generate (single PNG). Batch PDF covers single-SFID cases too.

    def cmd_stickers_batch(args):
        # Check optional deps (QR/Pillow) and ReportLab for PDF
        deps = st_check_dependencies()
        if not deps.get("qrcode"):
            print("[smallFactory] Error: 'qrcode' not installed. Try: pip install 'qrcode[pil]' pillow")
            sys.exit(1)
        try:
            from reportlab.pdfgen import canvas as rl_canvas
            from reportlab.lib.units import inch
            from reportlab.lib.utils import ImageReader
        except Exception:
            print("[smallFactory] Error: 'reportlab' is not installed. Install with: pip install -r web/requirements.txt or pip install reportlab")
            sys.exit(1)

        datarepo_path = _repo_path()

        # Collect SFIDs from --sfids/--file/stdin
        sfids: list[str] = []
        if args.sfids:
            if args.sfids.strip() == "-":
                # read from stdin
                src = sys.stdin.read()
            else:
                src = args.sfids
            # split by commas and newlines
            parts = []
            for chunk in src.splitlines():
                parts.extend(chunk.split(","))
            sfids.extend([p.strip() for p in parts if p.strip()])
        if args.file:
            try:
                with open(args.file, "r", encoding="utf-8") as fh:
                    src = fh.read()
                parts = []
                for chunk in src.splitlines():
                    parts.extend(chunk.split(","))
                sfids.extend([p.strip() for p in parts if p.strip()])
            except Exception as e:
                print(f"[smallFactory] Error reading --file '{args.file}': {e}")
                sys.exit(1)

        # Deduplicate while preserving order
        seen = set()
        sfids = [s for s in sfids if not (s in seen or seen.add(s))]
        if not sfids:
            print("[smallFactory] Error: No SFIDs provided. Use --sfids, --file, or '-' for stdin.")
            sys.exit(2)

        fields_list = None
        if args.fields:
            fields_list = [s.strip() for s in args.fields.split(",") if s.strip()]

        # Parse sizes
        size_px = _parse_size(args.size, args.dpi)
        w_in, h_in = _parse_size_inches(args.size)

        # Prepare PDF
        out_pdf = args.out or "stickers.pdf"
        c = rl_canvas.Canvas(out_pdf, pagesize=(w_in * inch, h_in * inch))

        import base64, io

        success = 0
        for sfid in sfids:
            try:
                result = st_generate_sticker_for_entity(
                    datarepo_path,
                    sfid,
                    fields=fields_list,
                    size=size_px,
                    dpi=args.dpi,
                )
                png_b64 = result.get("png_base64")
                if not png_b64:
                    raise RuntimeError("no image generated")
                png_bytes = base64.b64decode(png_b64)
                img_reader = ImageReader(io.BytesIO(png_bytes))
                # Fill entire page to achieve exact physical size
                c.drawImage(img_reader, 0, 0, width=w_in * inch, height=h_in * inch)
                c.showPage()
                success += 1
            except Exception as e:
                print(f"[smallFactory] Warning: failed to generate sticker for '{sfid}': {e}")

        if success == 0:
            print("[smallFactory] Error: Failed to generate any stickers; PDF not written.")
            sys.exit(1)

        try:
            c.save()
        except Exception as e:
            print(f"[smallFactory] Error writing PDF '{out_pdf}': {e}")
            sys.exit(1)

        fmt = _fmt()
        if fmt == "json":
            print(json.dumps({
                "output": os.path.abspath(out_pdf),
                "count": success,
                "page_size_in": {"width": w_in, "height": h_in},
                "dpi": args.dpi,
            }, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump({
                "output": os.path.abspath(out_pdf),
                "count": success,
                "page_size_in": {"width": w_in, "height": h_in},
                "dpi": args.dpi,
            }, sort_keys=False))
        else:
            print(f"[smallFactory] Wrote {out_pdf} with {success} page(s)")

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
    elif cmd == "stickers":
        sub = getattr(args, "st_cmd", None)
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
        ("stickers", None): cmd_stickers_batch,
        ("stickers", "batch"): cmd_stickers_batch,
    }

    handler = DISPATCH.get((cmd, sub))
    if handler:
        handler(args)
    else:
        if cmd == "inventory":
            inventory_parser.print_help()
        else:
            parser.print_help()

