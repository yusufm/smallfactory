import sys
import os
import argparse
import pathlib
import json
import yaml
import datetime

from smallfactory import __version__
from smallfactory.core.v1.config import (
    get_datarepo_path,
    CONFIG_FILENAME,
)
from smallfactory.core.v1 import repo as repo_ops
from smallfactory.core.v1.inventory import (
    inventory_post,
    inventory_onhand,
    inventory_onhand_readonly,
    inventory_rebuild,
)

# Entities core API
from smallfactory.core.v1.entities import (
    list_entities as ent_list_entities,
    get_entity as ent_get_entity,
    create_entity as ent_create_entity,
    update_entity_fields as ent_update_entity_fields,
    retire_entity as ent_retire_entity,
    # Revisions APIs
    bump_revision as ent_bump_revision,
    release_revision as ent_release_revision,
    # BOM APIs
    bom_list as ent_bom_list,
    bom_add_line as ent_bom_add_line,
    bom_remove_line as ent_bom_remove_line,
    bom_set_line as ent_bom_set_line,
    bom_alt_add as ent_bom_alt_add,
    bom_alt_remove as ent_bom_alt_remove,
    resolved_bom_tree as ent_resolved_bom_tree,
)
# Files core API
from smallfactory.core.v1.files import (
    list_files as f_list_files,
    mkdir as f_mkdir,
    rmdir as f_rmdir,
    upload_file as f_upload_file,
    delete_file as f_delete_file,
    move_file as f_move_file,
    move_dir as f_move_dir,
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
    init_parser.add_argument("--github-url", dest="github_url", default=None, help="GitHub repository URL to clone (optional)")
    init_parser.add_argument("--name", dest="name", default=None, help="Name for the new local datarepo when not cloning (optional)")

    # inventory group (nested subcommands)
    inventory_parser = subparsers.add_parser("inventory", help="Inventory operations")
    inv_sub = inventory_parser.add_subparsers(dest="inv_cmd", required=False, parser_class=SFArgumentParser)

    # post: append a journal entry
    inv_post = inv_sub.add_parser("post", help="Append an inventory journal entry for a part")
    inv_post.add_argument("--part", required=True, metavar="sfid", help="Part SFID (e.g., p_m3x10)")
    inv_post.add_argument("--qty-delta", dest="qty_delta", required=True, type=int, metavar="delta", help="Signed quantity delta (e.g., +5 or -2)")
    inv_post.add_argument("--l_sfid", required=False, metavar="l_sfid", help="Location SFID (e.g., l_a1). If omitted, uses sfdatarepo.yml: inventory.default_location")
    inv_post.add_argument("--reason", required=False, help="Optional reason string for the journal entry")

    # onhand: report on-hand quantities
    inv_onhand = inv_sub.add_parser("onhand", help="Report on-hand quantities (by part, by location, or summary)")
    grp = inv_onhand.add_mutually_exclusive_group(required=False)
    grp.add_argument("--part", metavar="sfid", help="Part SFID to report on")
    grp.add_argument("--l_sfid", metavar="l_sfid", help="Location SFID to report on")
    inv_onhand.add_argument("--readonly", action="store_true", help="Read-only mode: compute without writing caches")

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

    ent_set = ent_sub.add_parser(
        "set",
        help="Update fields for an entity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  sf entities set b_<build_sfid> serialnumber=SN123 datetime=2024-06-01T12:00:00Z\n\n"
            "Notes:\n"
            "  - For build entities, you can set 'serialnumber' and 'datetime' (ISO 8601)."
        ),
    )
    ent_set.add_argument("sfid", help="Entity SFID")
    ent_set.add_argument(
        "pairs",
        nargs="+",
        help=(
            "key=value fields to set (e.g., name=Widget; for builds: "
            "serialnumber=SN123 datetime=2024-06-01T12:00:00Z)"
        ),
    )

    ent_retire = ent_sub.add_parser("retire", help="Retire (soft-delete) an entity")
    ent_retire.add_argument("sfid", help="Entity SFID")
    ent_retire.add_argument("--reason", default=None, help="Retirement reason")

    # entities > build group (build-specific operations)
    ent_build = ent_sub.add_parser("build", help="Operations for build entities (finished goods)")
    build_sub = ent_build.add_subparsers(dest="build_cmd", required=False, parser_class=SFArgumentParser)

    b_serial = build_sub.add_parser("serial", help="Set serial number on a build entity")
    b_serial.add_argument("sfid", help="Build SFID (e.g., b_2024_0001)")
    b_serial.add_argument("value", help="Serial number value to set")

    b_dt = build_sub.add_parser("datetime", help="Set built-at datetime on a build entity (ISO 8601)")
    b_dt.add_argument("sfid", help="Build SFID (e.g., b_2024_0001)")
    b_dt.add_argument("value", help="ISO 8601 datetime (e.g., 2024-06-01T12:00:00Z)")

    # entities > revision group (revision management for parts)
    ent_rev = ent_sub.add_parser("revision", help="Revision operations for part entities")
    rev_sub = ent_rev.add_subparsers(dest="rev_cmd", required=False, parser_class=SFArgumentParser)

    ent_rev_bump = rev_sub.add_parser("bump", help="Create and immediately release the next revision for a part")
    ent_rev_bump.add_argument("sfid", help="Part SFID (e.g., p_widget)")
    ent_rev_bump.add_argument("--notes", default=None, help="Optional notes for revision metadata (applied to snapshot and release)")
    ent_rev_bump.add_argument("--released-at", dest="released_at", default=None, help="ISO datetime for release (default now)")

    ent_rev_release = rev_sub.add_parser("release", help="Mark a revision as released and update the 'released' pointer")
    ent_rev_release.add_argument("sfid", help="Part SFID (e.g., p_widget)")
    ent_rev_release.add_argument("rev", help="Revision label to release (e.g., A, B, ...)")
    ent_rev_release.add_argument("--released-at", dest="released_at", default=None, help="ISO datetime for release (default now)")
    ent_rev_release.add_argument("--notes", default=None, help="Optional release notes")

    # entities > files group (working files area)
    ent_files = ent_sub.add_parser("files", help="Manage files and folders (working area)")
    files_sub = ent_files.add_subparsers(dest="files_cmd", required=False, parser_class=SFArgumentParser)

    ef_ls = files_sub.add_parser("ls", help="List files and folders under files/")
    ef_ls.add_argument("sfid", help="Entity SFID")
    ef_ls.add_argument("--path", default=None, help="Relative path within files/ (optional)")
    ef_ls.add_argument("-r", "--recursive", action="store_true", help="Recursive listing")
    ef_ls.add_argument("--glob", default=None, help="Glob filter applied to relative paths")

    ef_mkdir = files_sub.add_parser("mkdir", help="Create a folder under files/")
    ef_mkdir.add_argument("sfid", help="Entity SFID")
    ef_mkdir.add_argument("path", help="Folder path to create (relative to files/)")

    ef_rmdir = files_sub.add_parser("rmdir", help="Remove an empty folder (only .gitkeep allowed)")
    ef_rmdir.add_argument("sfid", help="Entity SFID")
    ef_rmdir.add_argument("path", help="Folder path to remove (relative to files/)")

    ef_add = files_sub.add_parser("add", help="Upload a file into files/")
    ef_add.add_argument("sfid", help="Entity SFID")
    ef_add.add_argument("src", help="Local source filepath")
    ef_add.add_argument("dst", help="Destination path under files/ (e.g., foo/bar.ext)")
    ef_add.add_argument("--overwrite", action="store_true", help="Overwrite destination if exists")

    ef_rm = files_sub.add_parser("rm", help="Delete a file from files/")
    ef_rm.add_argument("sfid", help="Entity SFID")
    ef_rm.add_argument("path", help="File path to remove (relative to files/)")

    ef_mv = files_sub.add_parser("mv", help="Move/rename a file or folder within files/")
    ef_mv.add_argument("sfid", help="Entity SFID")
    ef_mv.add_argument("src", help="Source path (relative to files/)")
    ef_mv.add_argument("dst", help="Destination path (relative to files/)")
    ef_mv.add_argument("--dir", action="store_true", help="Treat paths as directories (move_dir)")
    ef_mv.add_argument("--overwrite", action="store_true", help="Overwrite destination if exists")

    # bom group (bill of materials ops)
    bom_parser = subparsers.add_parser("bom", help="Bill of Materials operations for part entities")
    bom_sub = bom_parser.add_subparsers(dest="bom_cmd", required=False, parser_class=SFArgumentParser)

    bom_ls = bom_sub.add_parser(
        "ls",
        aliases=["list"],
        help="List BOM tree for a parent part (recursive by default; limit with --max-depth)",
    )
    bom_ls.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    bom_ls.add_argument(
        "--max-depth",
        type=int,
        default=None,
        help="Optional max recursion depth (root level = 0; 0 = immediate children)",
    )

    bom_add = bom_sub.add_parser("add", help="Add a BOM line to a parent part")
    bom_add.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    bom_add.add_argument("--use", required=True, help="Child entity SFID to reference")
    bom_add.add_argument("--qty", type=int, default=1, help="Quantity (default 1)")
    bom_add.add_argument("--rev", default="released", help="Revision selector (default 'released')")
    bom_add.add_argument("--index", type=int, default=None, help="Insert at index (default append)")
    bom_add.add_argument("--alt", dest="alts", action="append", default=None, help="Alternate child SFID (repeatable)")
    bom_add.add_argument("--alternates-group", dest="alternates_group", default=None, help="Optional alternates group name")
    bom_add.add_argument("--no-check-exists", dest="check_exists", action="store_false", help="Do not enforce that child exists")

    bom_rm = bom_sub.add_parser("rm", aliases=["remove"], help="Remove a BOM line by index or by child sfid")
    grp_rm = bom_rm.add_mutually_exclusive_group(required=True)
    bom_rm.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    grp_rm.add_argument("--index", type=int, help="Index of BOM line to remove")
    grp_rm.add_argument("--use", help="Child SFID to remove (removes first match unless --all)")
    bom_rm.add_argument("--all", dest="remove_all", action="store_true", help="Remove all matching uses (with --use)")

    bom_set = bom_sub.add_parser("set", help="Edit a BOM line by index")
    bom_set.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    bom_set.add_argument("--index", type=int, required=True, help="Index of BOM line to edit")
    bom_set.add_argument("--use", help="Set child SFID")
    bom_set.add_argument("--qty", type=int, help="Set quantity")
    bom_set.add_argument("--rev", help="Set revision selector")
    bom_set.add_argument("--alternates-group", dest="alternates_group", help="Set alternates group")
    bom_set.add_argument("--no-check-exists", dest="check_exists", action="store_false", help="Do not enforce that child exists")

    bom_alt_add = bom_sub.add_parser("alt-add", help="Append an alternate to a BOM line")
    bom_alt_add.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    bom_alt_add.add_argument("--index", type=int, required=True, help="Index of BOM line")
    bom_alt_add.add_argument("--use", dest="alt_use", required=True, help="Alternate child SFID to add")
    bom_alt_add.add_argument("--no-check-exists", dest="check_exists", action="store_false", help="Do not enforce that alternate exists")

    bom_alt_rm = bom_sub.add_parser("alt-rm", help="Remove an alternate from a BOM line")
    bom_alt_rm.add_argument("parent", help="Parent part SFID (e.g., p_widget)")
    bom_alt_rm_grp = bom_alt_rm.add_mutually_exclusive_group(required=True)
    bom_alt_rm.add_argument("--index", type=int, required=True, help="Index of BOM line")
    bom_alt_rm_grp.add_argument("--alt-index", type=int, dest="alt_index", help="Alternate index to remove")
    bom_alt_rm_grp.add_argument("--alt-use", dest="alt_use", help="Alternate child SFID to remove")

    # web command (kept top-level)
    web_parser = subparsers.add_parser("web", help="Start the web UI server")
    web_parser.add_argument("--port", type=int, default=8080, help="Port to run the web server on (default: 8080)")
    web_parser.add_argument("--host", default="0.0.0.0", help="Host to bind the web server to (default: 0.0.0.0)")
    web_parser.add_argument("--debug", action="store_true", help="Run in debug mode with auto-reload")

    # validate command (repo linter)
    validate_parser = subparsers.add_parser("validate", help="Validate datarepo against PLM SPEC")
    validate_parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as errors")
    validate_parser.add_argument("--no-entities", dest="no_entities", action="store_true", help="Skip entities/ validation")
    validate_parser.add_argument("--no-inventory", dest="no_inventory", action="store_true", help="Skip inventory/ validation")
    validate_parser.add_argument("--no-git", dest="no_git", action="store_true", help="Skip Git commit metadata checks")
    validate_parser.add_argument("--git-commits", dest="git_commits", type=int, default=200, help="Limit number of recent commits to scan for required ::sfid:: tokens (0 = all)")

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
        elif cmd == "bom":
            bom_parser.print_help()
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
        # Prefer GitHub clone; prompt for URL first if not provided
        github_url = (getattr(args, "github_url", None) or "").strip() or None
        if github_url is None:
            try:
                gh_in = input("Enter GitHub repository URL to clone (or press Enter to create a local repo): ").strip()
                github_url = gh_in or None
            except Exception:
                github_url = None

        if args.path:
            target_path = pathlib.Path(args.path)
        else:
            datarepos_dir = pathlib.Path("datarepos")
            datarepos_dir.mkdir(exist_ok=True)
            if github_url:
                repo_name = github_url.split("/")[-1].replace(".git", "").strip() or None
            else:
                repo_name = (getattr(args, "name", None) or "").strip() or None
            if not repo_name:
                # Prompt for local name only if we are not cloning and no name provided
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

        # Create or clone the repo and scaffold per PLM spec
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

            # Ensure default location is present and configured in sfdatarepo.yml.
            # This is idempotent and safe for both newly created and cloned repos.
            try:
                repo_ops.scaffold_default_location(repo_path, "l_inbox")
            except Exception as e:
                print(f"[smallFactory] Warning: could not scaffold default location/config: {e}")
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

    def cmd_validate(args):
        datarepo_path = _repo_path()
        try:
            include_entities = not getattr(args, "no_entities", False)
            include_inventory = not getattr(args, "no_inventory", False)
            include_git = not getattr(args, "no_git", False)
            git_commit_limit = int(getattr(args, "git_commits", 200) or 0)
            result = validate_repo(
                datarepo_path,
                include_entities=include_entities,
                include_inventory=include_inventory,
                include_git=include_git,
                git_commit_limit=git_commit_limit,
            )
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
                location=getattr(args, "l_sfid", None),
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
            if getattr(args, "readonly", False):
                res = inventory_onhand_readonly(
                    datarepo_path,
                    part=getattr(args, "part", None),
                    location=getattr(args, "l_sfid", None),
                )
            else:
                res = inventory_onhand(
                    datarepo_path,
                    part=getattr(args, "part", None),
                    location=getattr(args, "l_sfid", None),
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
        fields = _parse_pairs(getattr(args, "pairs", []))
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

    def cmd_entities_build_serial(args):
        datarepo_path = _repo_path()
        try:
            ent = ent_update_entity_fields(datarepo_path, args.sfid, {"serialnumber": args.value})
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            print(f"[smallFactory] Set serialnumber on '{args.sfid}' to '{args.value}'")

    def cmd_entities_build_datetime(args):
        datarepo_path = _repo_path()
        # Basic ISO-8601 validation (accepts trailing Z)
        val = (args.value or "").strip()
        try:
            probe = val[:-1] + "+00:00" if val.endswith("Z") else val
            datetime.datetime.fromisoformat(probe)
        except Exception:
            print("[smallFactory] Error: invalid ISO 8601 datetime. Examples: 2024-06-01T12:00:00Z or 2024-06-01T12:00:00+00:00")
            sys.exit(2)
        try:
            ent = ent_update_entity_fields(datarepo_path, args.sfid, {"datetime": val})
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(ent, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(ent, sort_keys=False))
        else:
            print(f"[smallFactory] Set datetime on '{args.sfid}' to '{val}'")

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

    # Entities > Files handlers (working files area)
    def _print_or_dump(obj, human_line: str | None = None):
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(obj, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(obj, sort_keys=False))
        elif human_line is not None:
            print(human_line)

    def _files_root_name(datarepo_path: pathlib.Path, sfid: str) -> str:
        # Backward-compat for legacy 'design/' folder removed; always use 'files/'.
        return "files"


    def cmd_entities_files_ls(args):
        datarepo_path = _repo_path()
        try:
            res = f_list_files(
                datarepo_path,
                args.sfid,
                path=getattr(args, "path", None),
                recursive=bool(getattr(args, "recursive", False)),
                glob=getattr(args, "glob", None),
            )
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        if _fmt() == "human":
            base = _files_root_name(datarepo_path, args.sfid)
            rel = getattr(args, "path", None)
            print(f"{args.sfid} {base}{('/' + rel) if rel else ''}:")
            dirs = [i for i in res.get("items", []) if i.get("type") == "dir"]
            files = [i for i in res.get("items", []) if i.get("type") == "file"]
            for i in dirs:
                print(f"  dir  {i['path']}")
            for i in files:
                sz = i.get("size")
                print(f"  file {i['path']} ({sz} B)")
        else:
            _print_or_dump(res)

    def cmd_entities_files_mkdir(args):
        datarepo_path = _repo_path()
        try:
            res = f_mkdir(datarepo_path, args.sfid, path=args.path)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        root = _files_root_name(datarepo_path, args.sfid)
        _print_or_dump(res, human_line=f"[smallFactory] Created folder {root}/{args.path} on '{args.sfid}'")

    def cmd_entities_files_rmdir(args):
        datarepo_path = _repo_path()
        try:
            res = f_rmdir(datarepo_path, args.sfid, path=args.path)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        root = _files_root_name(datarepo_path, args.sfid)
        _print_or_dump(res, human_line=f"[smallFactory] Removed empty folder {root}/{args.path} on '{args.sfid}'")

    def cmd_entities_files_add(args):
        datarepo_path = _repo_path()
        src = pathlib.Path(args.src).expanduser()
        if not src.exists() or not src.is_file():
            print(f"[smallFactory] Error: source file not found: {src}")
            sys.exit(2)
        b = src.read_bytes()
        try:
            res = f_upload_file(
                datarepo_path,
                args.sfid,
                path=args.dst,
                file_bytes=b,
                overwrite=bool(getattr(args, "overwrite", False)),
            )
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        root = _files_root_name(datarepo_path, args.sfid)
        _print_or_dump(res, human_line=f"[smallFactory] Uploaded file to {root}/{args.dst} on '{args.sfid}'")

    def cmd_entities_files_rm(args):
        datarepo_path = _repo_path()
        try:
            res = f_delete_file(datarepo_path, args.sfid, path=args.path)
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        root = _files_root_name(datarepo_path, args.sfid)
        _print_or_dump(res, human_line=f"[smallFactory] Deleted file {root}/{args.path} on '{args.sfid}'")

    def cmd_entities_files_mv(args):
        datarepo_path = _repo_path()
        try:
            if bool(getattr(args, "dir", False)):
                res = f_move_dir(
                    datarepo_path,
                    args.sfid,
                    src=args.src,
                    dst=args.dst,
                    overwrite=bool(getattr(args, "overwrite", False)),
                )
            else:
                res = f_move_file(
                    datarepo_path,
                    args.sfid,
                    src=args.src,
                    dst=args.dst,
                    overwrite=bool(getattr(args, "overwrite", False)),
                )
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        kind = "folder" if bool(getattr(args, "dir", False)) else "file"
        root = _files_root_name(datarepo_path, args.sfid)
        _print_or_dump(res, human_line=f"[smallFactory] Moved {kind} within {root}: {args.src} -> {args.dst} on '{args.sfid}'")


    # Entities > Revision handlers
    def cmd_entities_rev_bump(args):
        datarepo_path = _repo_path()
        try:
            # Cut next snapshot (draft), then immediately release it
            res_bump = ent_bump_revision(datarepo_path, args.sfid, notes=getattr(args, "notes", None))
            new_rev = res_bump.get("new_rev")
            if not new_rev:
                raise RuntimeError("failed to determine new revision label")
            res = ent_release_revision(
                datarepo_path,
                args.sfid,
                new_rev,
                released_at=getattr(args, "released_at", None),
                notes=getattr(args, "notes", None),
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
            print(f"[smallFactory] Created and released revision '{res.get('rev')}' for '{args.sfid}'")

    def cmd_entities_rev_release(args):
        datarepo_path = _repo_path()
        try:
            res = ent_release_revision(
                datarepo_path,
                args.sfid,
                args.rev,
                released_at=getattr(args, "released_at", None),
                notes=getattr(args, "notes", None),
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
            print(f"[smallFactory] Released revision '{args.rev}' for '{args.sfid}' (current released: {res.get('rev')})")

    # BOM command handlers
    def _normalize_bom_line(line: dict) -> dict:
        out = dict(line or {})
        # compress None entries
        return {k: v for k, v in out.items() if v is not None}

    def _to_int_or_none(val):
        try:
            if isinstance(val, bool):
                return None
            return int(val)
        except Exception:
            return None

    def _walk_bom(datarepo_path: pathlib.Path, root_sfid: str, *, max_depth: int | None = None) -> list:
        """Use core resolved_bom_tree() and enrich with on-hand totals for CLI output.

        Returns nodes with fields compatible with previous CLI output:
        parent, use, name, qty, rev, level, is_alt, alternates_group, gross_qty, cycle, onhand_total.
        """
        core_nodes = ent_resolved_bom_tree(datarepo_path, root_sfid, max_depth=max_depth)
        onhand_cache: dict[str, int | None] = {}

        def get_onhand_total(sfid: str) -> int | None:
            if sfid in onhand_cache:
                return onhand_cache[sfid]
            try:
                if not sfid or not isinstance(sfid, str) or not sfid.startswith("p_"):
                    onhand_cache[sfid] = None
                    return None
                oh = inventory_onhand(datarepo_path, part=sfid)
                total = int(oh.get("total", 0)) if isinstance(oh, dict) else None
                onhand_cache[sfid] = total
                return total
            except Exception:
                onhand_cache[sfid] = None
                return None

        out: list = []
        for n in core_nodes:
            # Map core fields to CLI-compatible fields
            out.append({
                "parent": n.get("parent"),
                "use": n.get("use"),
                "name": n.get("name"),
                "qty": n.get("qty"),
                # CLI historically exposed the spec under 'rev'
                "rev": n.get("rev_spec", "released"),
                "level": n.get("level"),
                "is_alt": n.get("is_alt", False),
                "alternates_group": n.get("alternates_group"),
                "gross_qty": n.get("gross_qty"),
                "cycle": n.get("cycle", False),
                "onhand_total": get_onhand_total(n.get("use")),
            })
        return out

    def cmd_bom_ls(args):
        datarepo_path = _repo_path()
        try:
            nodes = _walk_bom(datarepo_path, args.parent, max_depth=getattr(args, "max_depth", None))
        except Exception as e:
            print(f"[smallFactory] Error: {e}")
            sys.exit(1)
        fmt = _fmt()
        if fmt == "json":
            print(json.dumps(nodes, indent=2))
        elif fmt == "yaml":
            print(yaml.safe_dump(nodes, sort_keys=False))
        else:
            if not nodes:
                print(f"[smallFactory] No BOM lines on '{args.parent}'")
            else:
                print(f"[smallFactory] Full BOM tree for '{args.parent}':")
                for n in nodes:
                    indent = "  " * n.get("level", 0)
                    tag = "[ALT] " if n.get("is_alt") else ""
                    use = n.get("use", "?")
                    name = n.get("name") or ""
                    show_name = f" [{name}]" if name and name != use else ""
                    qty = n.get("qty", 1)
                    rev = n.get("rev", "released")
                    gross = n.get("gross_qty")
                    gross_s = f" (gross={gross})" if gross is not None else ""
                    oh = n.get("onhand_total")
                    oh_s = f" onhand={oh}" if oh is not None else ""
                    print(f"{indent}- {tag}{qty} x {use}{show_name} rev={rev}{oh_s}{gross_s}")

    def cmd_bom_add(args):
        datarepo_path = _repo_path()
        alts = None
        if args.alts:
            alts = [{"use": a} for a in args.alts]
        try:
            res = ent_bom_add_line(
                datarepo_path,
                args.parent,
                use=args.use,
                qty=args.qty,
                rev=args.rev,
                alternates=alts,
                alternates_group=getattr(args, "alternates_group", None),
                index=args.index,
                check_exists=getattr(args, "check_exists", True),
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
            print(f"[smallFactory] Added BOM line to '{args.parent}' at index {res.get('index')}: use={args.use} qty={args.qty}")

    def cmd_bom_rm(args):
        datarepo_path = _repo_path()
        try:
            res = ent_bom_remove_line(
                datarepo_path,
                args.parent,
                index=getattr(args, "index", None),
                use=getattr(args, "use", None),
                remove_all=getattr(args, "remove_all", False),
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
            removed = res.get("removed")
            print(f"[smallFactory] Removed BOM line(s) from '{args.parent}': {removed}")

    def cmd_bom_set(args):
        datarepo_path = _repo_path()
        updates = {}
        for key in ("use", "qty", "rev", "alternates_group"):
            val = getattr(args, key, None)
            if val is not None:
                updates[key] = val
        if not updates:
            print("[smallFactory] Error: no fields to update (--use/--qty/--rev/--alternates-group)")
            sys.exit(2)
        try:
            res = ent_bom_set_line(
                datarepo_path,
                args.parent,
                index=args.index,
                updates=updates,
                check_exists=getattr(args, "check_exists", True),
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
            line = _normalize_bom_line(res.get("line", {}))
            print(f"[smallFactory] Updated BOM line {args.index} on '{args.parent}': {line}")

    def cmd_bom_alt_add(args):
        datarepo_path = _repo_path()
        try:
            res = ent_bom_alt_add(
                datarepo_path,
                args.parent,
                index=args.index,
                alt_use=args.alt_use,
                check_exists=getattr(args, "check_exists", True),
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
            print(f"[smallFactory] Added alternate {args.alt_use} to line {args.index} on '{args.parent}'")

    def cmd_bom_alt_rm(args):
        datarepo_path = _repo_path()
        try:
            res = ent_bom_alt_remove(
                datarepo_path,
                args.parent,
                index=args.index,
                alt_index=getattr(args, "alt_index", None),
                alt_use=getattr(args, "alt_use", None),
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
            print(f"[smallFactory] Removed alternate from line {args.index} on '{args.parent}'")

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
            # Be specific: only claim Flask is missing if that's the failing module
            missing = getattr(e, "name", "") or ""
            if missing == "flask":
                print("❌ Error: Flask is not installed.")
                print("   Install web dependencies: pip install -r web/requirements.txt")
            else:
                print(f"❌ Import error starting web UI: {e}")
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
        ent_sc = getattr(args, "ent_cmd", None)
        if ent_sc == "revision":
            sub = f"revision:{getattr(args, 'rev_cmd', None)}"
        elif ent_sc == "files":
            sub = f"files:{getattr(args, 'files_cmd', None)}"
        elif ent_sc == "build":
            sub = f"build:{getattr(args, 'build_cmd', None)}"
        else:
            sub = ent_sc
    elif cmd == "bom":
        sub = getattr(args, "bom_cmd", None)
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
        ("entities", "revision:bump"): cmd_entities_rev_bump,
        ("entities", "revision:release"): cmd_entities_rev_release,
        ("entities", "files:ls"): cmd_entities_files_ls,
        ("entities", "files:mkdir"): cmd_entities_files_mkdir,
        ("entities", "files:rmdir"): cmd_entities_files_rmdir,
        ("entities", "files:add"): cmd_entities_files_add,
        ("entities", "files:rm"): cmd_entities_files_rm,
        ("entities", "files:mv"): cmd_entities_files_mv,
        ("entities", "build:serial"): cmd_entities_build_serial,
        ("entities", "build:datetime"): cmd_entities_build_datetime,
        ("bom", "ls"): cmd_bom_ls,
        ("bom", "list"): cmd_bom_ls,
        ("bom", "add"): cmd_bom_add,
        ("bom", "rm"): cmd_bom_rm,
        ("bom", "remove"): cmd_bom_rm,
        ("bom", "set"): cmd_bom_set,
        ("bom", "alt-add"): cmd_bom_alt_add,
        ("bom", "alt-rm"): cmd_bom_alt_rm,
        ("stickers", None): cmd_stickers_batch,
        ("stickers", "batch"): cmd_stickers_batch,
    }

    handler = DISPATCH.get((cmd, sub))
    if handler:
        handler(args)
    else:
        if cmd == "inventory":
            inventory_parser.print_help()
        elif cmd == "entities":
            entities_parser.print_help()
        else:
            parser.print_help()


if __name__ == "__main__":
    main()
