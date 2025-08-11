import sys
import os
import argparse
import pathlib
import json
import yaml

from smallfactory import __version__
from smallfactory.core.v1.config import (
    get_datarepo_path,
    CONFIG_FILENAME,
)
from smallfactory.core.v1 import repo as repo_ops
from smallfactory.core.v1.inventory import (
    inventory_post,
    inventory_onhand,
    inventory_rebuild,
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

# Validator (PLM SPEC compliance)
from smallfactory.core.v1.validate import validate_repo


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
    inventory_parser = subparsers.add_parser("inventory", help="Inventory operations")
    inv_sub = inventory_parser.add_subparsers(dest="inv_cmd", required=False, parser_class=SFArgumentParser)

    # post: append a journal entry
    inv_post = inv_sub.add_parser("post", help="Append an inventory journal entry for a part")
    inv_post.add_argument("--part", required=True, metavar="sfid", help="Part SFID (e.g., p_m3x10)")
    inv_post.add_argument("--qty-delta", dest="qty_delta", required=True, type=int, metavar="delta", help="Signed quantity delta (e.g., +5 or -2)")
    inv_post.add_argument("--location", required=False, metavar="l_sfid", help="Location SFID (e.g., l_a1). If omitted, uses inventory/config.yml: default_location")
    inv_post.add_argument("--reason", required=False, help="Optional reason string for the journal entry")

    # onhand: report on-hand quantities
    inv_onhand = inv_sub.add_parser("onhand", help="Report on-hand quantities (by part, by location, or summary)")
    grp = inv_onhand.add_mutually_exclusive_group(required=False)
    grp.add_argument("--part", metavar="sfid", help="Part SFID to report on")
    grp.add_argument("--location", metavar="l_sfid", help="Location SFID to report on")

    # rebuild: rebuild caches from journals
    inv_rebuild = inv_sub.add_parser("rebuild", help="Rebuild inventory on-hand caches from journals")

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

    # validate command (repo linter)
    validate_parser = subparsers.add_parser("validate", help="Validate datarepo against PLM SPEC")
    validate_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors")

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
        "--text-size",
        dest="text_size",
        type=int,
        default=24,
        help="Base text size in pixels for label text (title ~1.2x). Default 24",
    )
    stickers_parser.add_argument(
        "-o",
        "--out",
        dest="out",
        default="stickers.pdf",
        help="Output PDF filename (default: stickers.pdf)",
    )

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
        "--text-size",
        dest="text_size",
        type=int,
        default=24,
        help="Base text size in pixels for label text (title ~1.2x). Default 24",
    )
    st_batch.add_argument(
        "-o",
        "--out",
        dest="out",
        default="stickers.pdf",
        help="Output PDF filename (default: stickers.pdf)",
    )

    # (Removed legacy dynamic inventory add args injection; not applicable in journal model)

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

        # Guard against collisions
        if target_path.exists():
            if os.listdir(str(target_path)):
                print(f"[smallFactory] Error: Target directory '{target_path}' already exists and is not empty.")
                sys.exit(1)
            if github_url:
                print(f"[smallFactory] Error: Target directory '{target_path}' already exists. Please provide a non-existing path or omit --path.")
                sys.exit(1)

    def cmd_validate(args):
        datarepo_path = _repo_path()
        try:
            result = validate_repo(datarepo_path)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        errors = int(result.get("errors", 0))
        warnings = int(result.get("warnings", 0))
        if fmt == "json":
            print(json.dumps(result, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(result, sort_keys=False))
        else:
            # Human report
            print(f"[smallFactory] Validation results for {datarepo_path}")
            print(f"Errors: {errors}, Warnings: {warnings}")
            for it in result.get("issues", []):
                sev = it.get("severity", "?")
                code = it.get("code", "?")
                path = it.get("path", "")
                msg = it.get("message", "")
                print(f" - [{sev.upper()}] {code} :: {path} :: {msg}")
        if errors > 0 or (getattr(args, "strict", False) and warnings > 0):
            sys.exit(1)

        try:
            # Ensure parent directory exists, but do not pre-create the clone directory
            target_path.parent.mkdir(parents=True, exist_ok=True)

            if github_url:
                repo_path = repo_ops.create_or_clone(target_path, github_url)
                has_remote = True
            else:
                repo_path = repo_ops.create_or_clone(target_path, None)
                has_remote = False

            # Write initial datarepo config and scaffold
            repo_ops.write_datarepo_config(repo_path)

            # Set as default datarepo in user config
            repo_ops.set_default_datarepo(repo_path)

            # Initial commit and optional push
            repo_ops.initial_commit_and_optional_push(repo_path, has_remote=has_remote)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)

        fmt = _fmt()
        if fmt == "json":
            print(json.dumps({"repo_path": str(repo_path), "remote": github_url or None}, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump({"repo_path": str(repo_path), "remote": github_url or None}, sort_keys=False))
        else:
            if github_url:
                print(f"[smallFactory] Cloned and initialized datarepo at '{repo_path}'")
            else:
                print(f"[smallFactory] Initialized new datarepo at '{repo_path}'")
            print(f"[smallFactory] Default datarepo set in '{CONFIG_FILENAME}'")

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
                    text_size=args.text_size,
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
                "text_size": args.text_size,
            }, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump({
                "output": os.path.abspath(out_pdf),
                "count": success,
                "page_size_in": {"width": w_in, "height": h_in},
                "dpi": args.dpi,
                "text_size": args.text_size,
            }, sort_keys=False))
        else:
            print(f"[smallFactory] Wrote {out_pdf} with {success} page(s)")

    def cmd_inventory_post(args):
        datarepo_path = _repo_path()
        try:
            res = inventory_post(
                datarepo_path,
                part=args.part,
                qty_delta=args.qty_delta,
                location=getattr(args, "location", None),
                reason=getattr(args, "reason", None),
            )
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(res, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(res, sort_keys=False))
        else:
            print(f"[smallFactory] Posted inventory delta {res['qty_delta']} for part '{res['part']}' at '{res['location']}' (txn {res['txn']})")

    def cmd_inventory_onhand(args):
        datarepo_path = _repo_path()
        try:
            res = inventory_onhand(
                datarepo_path,
                part=getattr(args, "part", None),
                location=getattr(args, "location", None),
            )
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(res, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(res, sort_keys=False))
        else:
            # Simple human-readable dump
            print(yaml.safe_dump(res, sort_keys=False))

    def cmd_inventory_rebuild(args):
        datarepo_path = _repo_path()
        try:
            res = inventory_rebuild(datarepo_path)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(res, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(res, sort_keys=False))
        else:
            parts = ", ".join(res.get("parts", [])) or "0 parts"
            locs = ", ".join(res.get("locations", [])) or "0 locations"
            print(f"[smallFactory] Rebuilt on-hand caches for {parts}; locations: {locs}")

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
            print("🔧 Git-native PLM for 1-4 person teams")
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

    # Dispatch via table
    cmd = args.command

    # Determine subcommand for the current group
    if cmd == "inventory":
        sub = getattr(args, "inv_cmd", None)
    elif cmd == "entities":
        sub = getattr(args, "ent_cmd", None)
    elif cmd == "stickers":
        sub = getattr(args, "st_cmd", None)
    else:
        sub = None

    DISPATCH = {
        ("init", None): cmd_init,
        ("web", None): cmd_web,
        ("validate", None): cmd_validate,
        ("inventory", "post"): cmd_inventory_post,
        ("inventory", "onhand"): cmd_inventory_onhand,
        ("inventory", "rebuild"): cmd_inventory_rebuild,
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


if __name__ == "__main__":
    main()
