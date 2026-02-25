from __future__ import annotations
import subprocess
from pathlib import Path
import yaml

from .config import (
    SF_TOOL_VERSION,
    DATAREPO_CONFIG_FILENAME,
    CONFIG_FILENAME,
    load_config,
    save_config,
    INVENTORY_DEFAULT_FIELD_SPECS,
    validate_sfid,
)
from .gitutils import git_commit_paths


# Default entity field specs for part type (sfid prefix 'p_')
PART_ENTITY_DEFAULT_FIELD_SPECS = {
    "category": {
        "description": "Category or family.",
        "regex": r"^$|^.{1,500}$",
        "required": False,
    },
    "description": {
        "description": "Extended freeform description of the part.",
        "multiline": True,
        "required": False,
    },
    "manufacturer": {
        "description": "Manufacturer name.",
        "regex": r"^$|^.{1,500}$",
        "required": False,
    },
    "mpn": {
        "description": "Manufacturer Part Number.",
        "regex": r"^[A-Za-z0-9 ._\-/#+]*$",
        "required": False,
    },
    "name": {
        "description": "Human-readable item name.",
        "regex": r"^.{1,200}$",
        "required": True,
    },
    "notes": {
        "description": "Additional notes.",
        "multiline": True,
        "required": False,
    },
    "spn": {
        "description": "Supplier Part Number.",
        "regex": r"^[A-Za-z0-9 ._\-/#+]*$",
        "required": False,
    },
    "vendor": {
        "description": "Preferred supplier/vendor.",
        "regex": r"^$|^.{1,500}$",
        "required": False,
    },
}


def init_local_repo(repo_path: Path) -> Path:
    repo_path = repo_path.expanduser().resolve()
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo_path)
    return repo_path


def set_remote(repo_path: Path, remote_url: str) -> None:
    if remote_url:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=repo_path)


def write_datarepo_config(repo_path: Path) -> Path:
    datarepo_config = {
        "smallfactory_version": SF_TOOL_VERSION,
        "inventory": {
            "fields": INVENTORY_DEFAULT_FIELD_SPECS,
        },
        "entities": {
            "types": {
                # part entities (sfid prefix 'p_')
                "p": {
                    "fields": PART_ENTITY_DEFAULT_FIELD_SPECS,
                }
            }
        },
    }
    config_file = repo_path / DATAREPO_CONFIG_FILENAME
    with open(config_file, "w") as f:
        f.write(
            "# This file is a generated scaffold by smallFactory.\n"
            "# It is safe to edit and customize for your repository (Please conform to smallFactory specs).\n"
        )
        yaml.safe_dump(datarepo_config, f)
    # Ensure standard directories exist per SPECIFICATION.md
    inventory_dir = repo_path / "inventory"
    entities_dir = repo_path / "entities"
    inventory_dir.mkdir(parents=True, exist_ok=True)
    entities_dir.mkdir(parents=True, exist_ok=True)
    # inventory.default_location is set later by scaffold_default_location() in sfdatarepo.yml
    # Ensure recommended .gitattributes for inventory journals (idempotent)
    gia = repo_path / ".gitattributes"
    union_line = "inventory/p_*/journal.ndjson merge=union\n"
    try:
        if gia.exists():
            content = gia.read_text()
            if union_line.strip() not in content:
                with open(gia, "a") as gf:
                    gf.write("\n# smallFactory recommended union merge for inventory journals\n")
                    gf.write(union_line)
        else:
            with open(gia, "w") as gf:
                gf.write("# Git attributes for smallFactory datarepo\n")
                gf.write("# Use union merge for inventory journals to reduce conflicts\n")
                gf.write(union_line)
    except Exception:
        # Non-fatal; validator will still recommend adding this
        pass
    return config_file


def set_default_datarepo(repo_path: Path) -> None:
    cfg = load_config()
    cfg["default_datarepo"] = str(repo_path)
    save_config(cfg)


def initial_commit_and_optional_push(repo_path: Path, has_remote: bool) -> None:
    # Only commit the repo config file and .gitattributes; avoid touching
    # entities/ or inventory/ so the initial commit doesn't require ::sfid:: tokens.
    subprocess.run(["git", "add", DATAREPO_CONFIG_FILENAME], cwd=repo_path)
    gia = repo_path / ".gitattributes"
    if gia.exists():
        subprocess.run(["git", "add", ".gitattributes"], cwd=repo_path)
    subprocess.run(["git", "commit", "-m", "Initial smallFactory datarepo config"], cwd=repo_path)
    remotes = subprocess.run(["git", "remote"], cwd=repo_path, capture_output=True, text=True)
    if has_remote and "origin" in remotes.stdout:
        subprocess.run(["git", "branch", "-M", "main"], cwd=repo_path)
        try:
            subprocess.run(["git", "push", "-u", "origin", "main"], cwd=repo_path)
        except Exception:
            print("[smallFactory] Warning: Could not push to GitHub remote.")


def scaffold_default_location(repo_path: Path, location_sfid: str = "l_inbox") -> None:
    """Create a minimal default location entity and set default in sfdatarepo.yml.

    - Writes entities/<location_sfid>/entity.yml if missing.
    - Ensures sfdatarepo.yml has inventory.default_location=<location_sfid> (sets or updates).
    - Commits touched files with a message including ::sfid::<location_sfid>.
    - Idempotent: skips commit if nothing changed.
    """
    try:
        validate_sfid(location_sfid)
    except Exception as e:
        raise ValueError(f"Invalid default location sfid '{location_sfid}': {e}")

    ent_fp = repo_path / "entities" / location_sfid / "entity.yml"
    dr_cfg_fp = repo_path / DATAREPO_CONFIG_FILENAME

    # Track which paths we actually create
    created_paths = []

    # Ensure parent dir for entity
    ent_fp.parent.mkdir(parents=True, exist_ok=True)

    # Create minimal location entity if absent
    if not ent_fp.exists():
        ent_fp.write_text("name: Inbox\n")
        created_paths.append(ent_fp)

    # Ensure sfdatarepo.yml has inventory.default_location
    dr_changed = False
    dr_data = {}
    if dr_cfg_fp.exists():
        try:
            with open(dr_cfg_fp) as f:
                dr_data = yaml.safe_load(f) or {}
        except Exception:
            dr_data = {}
    inv_block = dr_data.get("inventory") or {}
    cur = inv_block.get("default_location")
    if cur != location_sfid:
        inv_block["default_location"] = location_sfid
        dr_data["inventory"] = inv_block
        with open(dr_cfg_fp, "w") as f:
            yaml.safe_dump(dr_data, f, sort_keys=False)
        created_paths.append(dr_cfg_fp)
        dr_changed = True

    if not created_paths:
        return  # nothing to do

    msg = (
        f"[smallFactory] Scaffold default location {location_sfid} and set repo default\n"
        f"::sfid::{location_sfid}"
    )
    try:
        git_commit_paths(repo_path, created_paths, msg)
    except Exception:
        print("[smallFactory] Warning: Failed to commit scaffolded default location/config.")


def create_or_clone(target_path: Path, github_url: str | None) -> Path:
    if github_url:
        subprocess.run(["git", "clone", github_url, str(target_path)], check=True)
        return target_path.expanduser().resolve()
    return init_local_repo(target_path)
