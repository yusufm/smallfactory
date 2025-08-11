from __future__ import annotations
from pathlib import Path
from typing import Dict, List
import json
import yaml
import re
import subprocess

from .config import validate_sfid, load_datarepo_config

ULID_RE = re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$")


def _rel(p: Path, root: Path) -> str:
    try:
        return str(p.relative_to(root))
    except Exception:
        return str(p)


def _load_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def _scan_entities(repo: Path, issues: List[Dict]) -> None:
    ent_root = repo / "entities"
    if not ent_root.exists():
        issues.append({
            "severity": "error",
            "code": "ENT_ROOT_MISSING",
            "path": "entities/",
            "message": "Missing entities/ directory"
        })
        return
    # Disallow single-file layout
    for yml in ent_root.glob("*.yml"):
        issues.append({
            "severity": "error",
            "code": "ENT_LAYOUT_SINGLE_FILE",
            "path": _rel(yml, repo),
            "message": "Entity must live under entities/<sfid>/entity.yml (directory layout), not a single YAML file"
        })
    # Validate directory layout
    for child in sorted([p for p in ent_root.iterdir() if p.is_dir()]):
        sfid = child.name
        try:
            validate_sfid(sfid)
        except Exception as e:
            issues.append({
                "severity": "error",
                "code": "ENT_SFID_INVALID",
                "path": f"entities/{sfid}/",
                "message": f"Invalid sfid directory name: {e}"
            })
            continue
        entity_yml = child / "entity.yml"
        if not entity_yml.exists():
            issues.append({
                "severity": "error",
                "code": "ENT_ENTITY_YML_MISSING",
                "path": f"entities/{sfid}/",
                "message": "Missing entity.yml"
            })
            continue
        try:
            data = _load_yaml(entity_yml)
        except Exception:
            issues.append({
                "severity": "error",
                "code": "ENT_ENTITY_YML_INVALID",
                "path": _rel(entity_yml, repo),
                "message": "entity.yml is not valid YAML or not a mapping"
            })
            continue
        if not isinstance(data, dict):
            issues.append({
                "severity": "error",
                "code": "ENT_ENTITY_YML_INVALID",
                "path": _rel(entity_yml, repo),
                "message": "entity.yml must be a YAML mapping"
            })
            continue
        if "sfid" in data:
            issues.append({
                "severity": "error",
                "code": "ENT_NO_SFID_FIELD",
                "path": _rel(entity_yml, repo),
                "message": "Do not include 'sfid' in entity.yml; identity is the directory name"
            })
        if "children" in data:
            issues.append({
                "severity": "error",
                "code": "ENT_NO_CHILDREN",
                "path": _rel(entity_yml, repo),
                "message": "Legacy key 'children' is not allowed; use 'bom'"
            })
        is_part = sfid.startswith("p_")
        if not is_part and "bom" in data:
            issues.append({
                "severity": "error",
                "code": "ENT_BOM_NON_PART",
                "path": _rel(entity_yml, repo),
                "message": "'bom' is only allowed on parts (sfid starting with 'p_')"
            })
        # Missing 'uom' on parts is allowed; defaults to 'ea' at read time per SPEC.


def _scan_inventory(repo: Path, issues: List[Dict]) -> None:
    inv_root = repo / "inventory"
    if not inv_root.exists():
        # Inventory optional; warn only
        issues.append({
            "severity": "warning",
            "code": "INV_ROOT_MISSING",
            "path": "inventory/",
            "message": "No inventory/ directory found (ok if not used)"
        })
        return
    # Check union merge recommendation
    gia = repo / ".gitattributes"
    if gia.exists():
        try:
            content = gia.read_text()
            if "inventory/p_*/journal.ndjson merge=union" not in content:
                issues.append({
                    "severity": "warning",
                    "code": "INV_UNION_MERGE_MISSING",
                    "path": ".gitattributes",
                    "message": "Recommend union merge for inventory journals: add 'inventory/p_*/journal.ndjson merge=union'"
                })
        except Exception:
            pass
    else:
        issues.append({
            "severity": "warning",
            "code": "INV_GITATTRIBUTES_MISSING",
            "path": ".gitattributes",
            "message": "Recommend adding .gitattributes with union merge for inventory journals"
        })

    # Optional default location in repo config (sfdatarepo.yml)
    try:
        dr_cfg = load_datarepo_config(repo)
        inv = dr_cfg.get("inventory") or {}
        loc = inv.get("default_location")
        if isinstance(loc, str) and loc:
            if not loc.startswith("l_"):
                issues.append({
                    "severity": "error",
                    "code": "INV_DEFAULT_LOCATION_INVALID",
                    "path": "sfdatarepo.yml",
                    "message": "sfdatarepo.yml: inventory.default_location must be an 'l_*' sfid"
                })
            else:
                try:
                    validate_sfid(loc)
                except Exception as e:
                    issues.append({
                        "severity": "error",
                        "code": "INV_DEFAULT_LOCATION_INVALID",
                        "path": "sfdatarepo.yml",
                        "message": f"sfdatarepo.yml: invalid inventory.default_location sfid: {e}"
                    })
                if not (repo / "entities" / loc / "entity.yml").exists():
                    issues.append({
                        "severity": "error",
                        "code": "INV_DEFAULT_LOCATION_MISSING_ENTITY",
                        "path": f"entities/{loc}/entity.yml",
                        "message": f"Default location '{loc}' not found under entities/"
                    })
    except Exception:
        # If config unreadable, skip; other validators will surface config file issues
        pass

    # Validate per-part journals
    for p_dir in sorted([p for p in inv_root.iterdir() if p.is_dir() and p.name.startswith("p_")]):
        part = p_dir.name
        # entity exists
        if not (repo / "entities" / part / "entity.yml").exists():
            issues.append({
                "severity": "error",
                "code": "INV_PART_ENTITY_MISSING",
                "path": f"inventory/{part}/",
                "message": f"No corresponding entity at entities/{part}/entity.yml"
            })
        j = p_dir / "journal.ndjson"
        if not j.exists():
            issues.append({
                "severity": "error",
                "code": "INV_JOURNAL_MISSING",
                "path": f"inventory/{part}/",
                "message": "Missing journal.ndjson for part inventory"
            })
            continue
        try:
            with open(j) as f:
                for idx, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        issues.append({
                            "severity": "error",
                            "code": "INV_JOURNAL_JSON",
                            "path": _rel(j, repo),
                            "message": f"Line {idx}: invalid JSON"
                        })
                        continue
                    if not isinstance(obj, dict):
                        issues.append({
                            "severity": "error",
                            "code": "INV_JOURNAL_OBJ",
                            "path": _rel(j, repo),
                            "message": f"Line {idx}: entry must be a JSON object"
                        })
                        continue
                    # Required keys
                    if "txn" not in obj:
                        issues.append({
                            "severity": "error",
                            "code": "INV_JOURNAL_TXN_REQUIRED",
                            "path": _rel(j, repo),
                            "message": f"Line {idx}: missing 'txn' (ULID)"
                        })
                    else:
                        tx = str(obj.get("txn"))
                        if not ULID_RE.fullmatch(tx):
                            issues.append({
                                "severity": "error",
                                "code": "INV_JOURNAL_TXN_FORMAT",
                                "path": _rel(j, repo),
                                "message": f"Line {idx}: 'txn' must be a ULID (26 chars Crockford base32)"
                            })
                    if "qty_delta" not in obj:
                        issues.append({
                            "severity": "error",
                            "code": "INV_JOURNAL_QTY_REQUIRED",
                            "path": _rel(j, repo),
                            "message": f"Line {idx}: missing 'qty_delta'"
                        })
                    # Forbidden keys
                    for forbidden in ("ts", "uom"):
                        if forbidden in obj:
                            issues.append({
                                "severity": "error",
                                "code": "INV_JOURNAL_FORBIDDEN_FIELD",
                                "path": _rel(j, repo),
                                "message": f"Line {idx}: field '{forbidden}' is not allowed"
                            })
                    # Optional location
                    loc = obj.get("location")
                    if loc is not None:
                        if not isinstance(loc, str) or not loc.startswith("l_"):
                            issues.append({
                                "severity": "error",
                                "code": "INV_LOCATION_INVALID",
                                "path": _rel(j, repo),
                                "message": f"Line {idx}: 'location' must be an 'l_*' sfid"
                            })
                        else:
                            try:
                                validate_sfid(loc)
                            except Exception as e:
                                issues.append({
                                    "severity": "error",
                                    "code": "INV_LOCATION_SFID_INVALID",
                                    "path": _rel(j, repo),
                                    "message": f"Line {idx}: invalid location sfid: {e}"
                                })
                            if not (repo / "entities" / loc / "entity.yml").exists():
                                issues.append({
                                    "severity": "error",
                                    "code": "INV_LOCATION_ENTITY_MISSING",
                                    "path": _rel(j, repo),
                                    "message": f"Line {idx}: location '{loc}' does not exist under entities/"
                                })
        except Exception:
            issues.append({
                "severity": "error",
                "code": "INV_JOURNAL_READ",
                "path": _rel(j, repo),
                "message": "Could not read journal.ndjson"
            })


def validate_repo(repo_path: Path) -> Dict:
    """Validate repository structure and content against PLM_SPEC.

    Returns a dict: { errors: int, warnings: int, issues: [ {severity, code, path, message} ] }
    """
    issues: List[Dict] = []
    _scan_entities(repo_path, issues)
    _scan_inventory(repo_path, issues)
    _scan_git_commits(repo_path, issues)

    errors = sum(1 for i in issues if i.get("severity") == "error")
    warnings = sum(1 for i in issues if i.get("severity") == "warning")
    return {"errors": errors, "warnings": warnings, "issues": issues}


def _scan_git_commits(repo: Path, issues: List[Dict]) -> None:
    """Scan recent commits for required commit metadata tokens when mutating PLM data.

    Rule: Any commit that changes files under entities/ or inventory/ must include
    at least one '::sfid::<SFID>' token in its commit message.
    """
    def _git(args: List[str]) -> str:
        return subprocess.check_output(["git", "-C", str(repo)] + args, text=True)

    try:
        # Verify repo
        _git(["rev-parse", "--is-inside-work-tree"])  # raises if not a git repo
        # Get recent hashes
        hashes = [h.strip() for h in _git(["log", "-n", "200", "--pretty=format:%H"]).splitlines() if h.strip()]
        for h in hashes:
            try:
                show = _git(["show", "--name-only", "--pretty=%B", h])
            except Exception:
                continue
            # Split message and file list: commit message first until a blank line before diff/file list
            parts = show.splitlines()
            # Commit message lines until we hit an empty line followed by file names or diff markers
            msg_lines: List[str] = []
            files: List[str] = []
            collecting_files = False
            for ln in parts:
                if not collecting_files:
                    if ln.strip() == "":
                        collecting_files = True
                        continue
                    msg_lines.append(ln)
                else:
                    if ln and not ln.startswith("    ") and not ln.startswith("diff --git"):
                        files.append(ln.strip())
            msg = "\n".join(msg_lines)
            touched_plm = any(f.startswith("entities/") or f.startswith("inventory/") for f in files)
            if not touched_plm:
                continue
            if "::sfid::" not in msg:
                issues.append({
                    "severity": "error",
                    "code": "GIT_TOKEN_REQUIRED",
                    "path": f"commit {h[:12]}",
                    "message": "Commits touching entities/ or inventory/ must include at least one '::sfid::<SFID>' token"
                })
    except Exception:
        issues.append({
            "severity": "warning",
            "code": "GIT_CHECK_SKIPPED",
            "path": str(repo),
            "message": "Git not available or not a repository; skipping commit metadata checks"
        })
