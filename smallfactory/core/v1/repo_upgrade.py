from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional
import json
import ntpath
import os
import shutil
import subprocess

import yaml

from .config import (
    DATAREPO_CONFIG_FILENAME,
    SF_TOOL_VERSION,
    validate_sfid,
)
from .locks import repo_process_lock, upgrade_in_progress_marker
from .validate import validate_repo


@dataclass(frozen=True)
class Migration:
    id: str
    introduced_version: str
    description: str
    is_needed: Callable[[Path], bool]
    apply: Callable[[Path], Dict]


def _load_yaml_map(p: Path) -> Dict:
    if not p.exists():
        return {}
    with open(p, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        return {}
    return data


def _write_yaml_map(p: Path, data: Dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def _repo_cfg_path(repo_path: Path) -> Path:
    return repo_path / DATAREPO_CONFIG_FILENAME


def _normalize_applied_migrations(raw) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    seen = set()
    for item in raw:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _normalize_repo_metadata(cfg: Dict) -> Dict:
    out = dict(cfg or {})
    version = str(out.get("smallfactory_version") or out.get("compat_version") or SF_TOOL_VERSION).strip()
    if not version:
        version = SF_TOOL_VERSION
    out["smallfactory_version"] = version
    out["applied_migrations"] = _normalize_applied_migrations(out.get("applied_migrations"))
    return out


def _parse_semver_like(raw: str) -> tuple[int, int, int]:
    s = str(raw or "").strip()
    if not s:
        return (0, 0, 0)
    parts = s.split(".")
    nums: List[int] = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except Exception:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _version_state(repo_version: str) -> str:
    rv = _parse_semver_like(repo_version)
    tv = _parse_semver_like(SF_TOOL_VERSION)
    if rv < tv:
        return "repo_older"
    if rv > tv:
        return "repo_newer"
    return "match"


def _known_migration_ids() -> List[str]:
    return [m.id for m in MIGRATIONS]


def _entity_dirs(repo_path: Path) -> List[Path]:
    root = repo_path / "entities"
    if not root.exists() or not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()])


def _entity_yml_paths(repo_path: Path) -> List[Path]:
    out: List[Path] = []
    for d in _entity_dirs(repo_path):
        fp = d / "entity.yml"
        if fp.exists() and fp.is_file():
            out.append(fp)
    return out


_LOCK_GITIGNORE_PATTERNS = [
    ".smallfactory.repo.lock",
    ".smallfactory.repo.lock.*",
    "**/.smallfactory.repo.lock.*",
    "inventory/**/*.lock",
]


def _cleanup_transient_lock_artifacts(repo_path: Path) -> List[str]:
    """Remove stale transient lock artifacts in working tree paths."""
    touched: List[str] = []
    # Legacy repo-root lock artifacts.
    for p in sorted(repo_path.glob(".smallfactory.repo.lock*")):
        if p.is_file():
            p.unlink(missing_ok=True)
            touched.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    # Nested legacy lock files outside .git (e.g., inventory/**/.smallfactory.repo.lock.*).
    for p in sorted(repo_path.rglob(".smallfactory.repo.lock.*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            p.unlink(missing_ok=True)
            touched.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    # Legacy inventory lock files.
    inv_root = repo_path / "inventory"
    if inv_root.exists() and inv_root.is_dir():
        for p in sorted(inv_root.rglob("*.lock")):
            if p.is_file():
                p.unlink(missing_ok=True)
                touched.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    return sorted(set(touched))


def _stale_transient_lock_artifacts(repo_path: Path) -> List[str]:
    out: List[str] = []
    for p in sorted(repo_path.glob(".smallfactory.repo.lock*")):
        if p.is_file():
            out.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    for p in sorted(repo_path.rglob(".smallfactory.repo.lock.*")):
        if ".git" in p.parts:
            continue
        if p.is_file():
            out.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    inv_root = repo_path / "inventory"
    if inv_root.exists() and inv_root.is_dir():
        for p in sorted(inv_root.rglob("*.lock")):
            if p.is_file():
                out.append(str(p.relative_to(repo_path)).replace("\\", "/"))
    return sorted(set(out))


def _needs_entity_files_flat_to_dir(repo_path: Path) -> bool:
    entities_root = repo_path / "entities"
    if not entities_root.exists() or not entities_root.is_dir():
        return False
    for flat in sorted([p for p in entities_root.glob("*.yml") if p.is_file()]):
        try:
            validate_sfid(flat.stem)
            return True
        except Exception:
            continue
    return False


def _migration_entity_files_flat_to_dir(repo_path: Path) -> Dict:
    touched: List[str] = []
    entities_root = repo_path / "entities"
    if not entities_root.exists() or not entities_root.is_dir():
        return {"changed": False, "touched": touched}

    flat_files = sorted([p for p in entities_root.glob("*.yml") if p.is_file()])
    changed = False
    for flat in flat_files:
        sfid = flat.stem
        try:
            validate_sfid(sfid)
        except Exception:
            continue

        target_dir = entities_root / sfid
        target_file = target_dir / "entity.yml"
        if target_file.exists():
            old_data = _load_yaml_map(flat)
            new_data = _load_yaml_map(target_file)
            if old_data == new_data:
                flat.unlink()
                touched.append(str(flat.relative_to(repo_path)).replace("\\", "/"))
                changed = True
                continue
            raise ValueError(
                f"Migration conflict: both '{flat}' and '{target_file}' exist with different content"
            )

        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(flat), str(target_file))
        touched.append(str(flat.relative_to(repo_path)).replace("\\", "/"))
        touched.append(str(target_file.relative_to(repo_path)).replace("\\", "/"))
        changed = True

    return {"changed": changed, "touched": sorted(set(touched))}


def _needs_design_to_files(repo_path: Path) -> bool:
    for ent_dir in _entity_dirs(repo_path):
        if (ent_dir / "design").is_dir():
            return True
    return False


def _move_tree_merge(src: Path, dst: Path) -> List[str]:
    touched: List[str] = []
    dst.mkdir(parents=True, exist_ok=True)

    # Create destination directories first.
    for p in sorted([x for x in src.rglob("*") if x.is_dir()], key=lambda x: len(x.parts)):
        rel = p.relative_to(src)
        (dst / rel).mkdir(parents=True, exist_ok=True)

    # Move files; reject conflicts where content differs.
    for p in sorted([x for x in src.rglob("*") if x.is_file()]):
        rel = p.relative_to(src)
        q = dst / rel
        q.parent.mkdir(parents=True, exist_ok=True)
        if q.exists():
            if q.is_dir():
                raise ValueError(f"Migration conflict: '{q}' is a directory")
            if p.read_bytes() != q.read_bytes():
                raise ValueError(f"Migration conflict: file differs at '{q}'")
            p.unlink()
        else:
            shutil.move(str(p), str(q))
            touched.append(str(q))

    # Remove now-empty source tree from leaves to root.
    dirs = sorted([x for x in src.rglob("*") if x.is_dir()], key=lambda x: len(x.parts), reverse=True)
    for d in dirs:
        try:
            d.rmdir()
        except OSError:
            pass
    try:
        src.rmdir()
    except OSError:
        pass

    return touched


def _migration_design_to_files(repo_path: Path) -> Dict:
    touched: List[str] = []
    changed = False
    for ent_dir in _entity_dirs(repo_path):
        design_dir = ent_dir / "design"
        if not design_dir.exists() or not design_dir.is_dir():
            continue
        files_dir = ent_dir / "files"
        if not files_dir.exists():
            design_dir.rename(files_dir)
            touched.append(str(files_dir.relative_to(repo_path)).replace("\\", "/"))
            changed = True
            continue

        moved = _move_tree_merge(design_dir, files_dir)
        if moved:
            touched.extend([str(Path(m).relative_to(repo_path)).replace("\\", "/") for m in moved])
            changed = True
        if design_dir.exists():
            # Best effort cleanup if merge removed all contents.
            try:
                design_dir.rmdir()
                touched.append(str(design_dir.relative_to(repo_path)).replace("\\", "/"))
                changed = True
            except OSError:
                pass

    return {"changed": changed, "touched": sorted(set(touched))}


def _merge_bom_lists(existing, legacy):
    if not isinstance(existing, list) or not isinstance(legacy, list):
        return existing, False
    keys = set()
    out = list(existing)
    for item in existing:
        keys.add(json.dumps(item, sort_keys=True, default=str))
    changed = False
    for item in legacy:
        k = json.dumps(item, sort_keys=True, default=str)
        if k in keys:
            continue
        keys.add(k)
        out.append(item)
        changed = True
    return out, changed


def _migration_children_to_bom(repo_path: Path) -> Dict:
    touched: List[str] = []
    changed = False
    for fp in _entity_yml_paths(repo_path):
        data = _load_yaml_map(fp)
        if "children" not in data:
            continue

        legacy = data.pop("children")
        local_changed = True

        if "bom" not in data or data.get("bom") in (None, [], {}):
            data["bom"] = legacy
        elif data.get("bom") == legacy:
            pass
        else:
            merged, merged_changed = _merge_bom_lists(data.get("bom"), legacy)
            if merged_changed:
                data["bom"] = merged
            elif data.get("bom") != legacy:
                raise ValueError(f"Migration conflict: could not merge children->bom for '{fp}'")

        if local_changed:
            _write_yaml_map(fp, data)
            touched.append(str(fp.relative_to(repo_path)).replace("\\", "/"))
            changed = True

    return {"changed": changed, "touched": sorted(set(touched))}


def _needs_children_to_bom(repo_path: Path) -> bool:
    for fp in _entity_yml_paths(repo_path):
        data = _load_yaml_map(fp)
        if "children" in data:
            return True
    return False


def _normalize_event_tags(raw) -> List[str]:
    src = raw
    if src is None:
        return []
    if isinstance(src, str):
        src = [x.strip() for x in src.split(",")]
    if not isinstance(src, list):
        return []
    out: List[str] = []
    seen = set()
    for item in src:
        t = str(item or "").strip().lower()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def _normalize_event_files(raw) -> List[str]:
    src = raw
    if src is None:
        return []
    if isinstance(src, str):
        src = [src]
    if not isinstance(src, list):
        return []
    out: List[str] = []
    seen = set()
    for item in src:
        p = str(item or "").strip().replace("\\", "/")
        if not p:
            continue
        if os.path.isabs(p) or ntpath.isabs(p):
            continue
        if ".." in p.split("/"):
            continue
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def _event_id_or_generated(raw_id, *, used_ids: set[str], fallback_base: str) -> str:
    rid = str(raw_id or "").strip()
    if rid:
        try:
            validate_sfid(rid)
        except Exception:
            rid = ""
    base = rid or fallback_base
    cand = base
    i = 1
    while True:
        try:
            validate_sfid(cand)
        except Exception:
            cand = f"{base}_{i}"
            i += 1
            continue
        if cand not in used_ids:
            used_ids.add(cand)
            return cand
        cand = f"{base}_{i}"
        i += 1


def _read_events_jsonl(fp: Path) -> List[Dict]:
    if not fp.exists():
        return []
    out: List[Dict] = []
    with open(fp, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    return out


def _write_events_jsonl(fp: Path, rows: List[Dict]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True))
            f.write("\n")


def _migration_build_events_to_jsonl(repo_path: Path) -> Dict:
    touched: List[str] = []
    changed = False
    now_iso = datetime.now(timezone.utc).isoformat()

    for fp in _entity_yml_paths(repo_path):
        sfid = fp.parent.name
        if not sfid.startswith("b_"):
            continue

        data = _load_yaml_map(fp)
        legacy_events = data.get("events")
        if not isinstance(legacy_events, list):
            continue

        events_fp = fp.parent / "events.jsonl"
        existing = _read_events_jsonl(events_fp)
        used_ids = set()
        for ev in existing:
            rid = str(ev.get("id") or "").strip()
            if rid:
                used_ids.add(rid)

        migrated: List[Dict] = []
        for idx, raw in enumerate(legacy_events):
            if not isinstance(raw, dict):
                continue
            rec = dict(raw)
            ev_id = _event_id_or_generated(
                rec.get("id"),
                used_ids=used_ids,
                fallback_base=f"evt_migrated_{idx + 1}",
            )
            out: Dict = {
                "id": ev_id,
                "ts": str(rec.get("ts") or "").strip() or now_iso,
                "tags": _normalize_event_tags(rec.get("tags")),
            }
            msg = str(rec.get("message") or "").strip()
            if msg:
                out["message"] = msg
            files = _normalize_event_files(rec.get("files"))
            if files:
                out["files"] = files
            migrated.append(out)

        # Preserve any existing events first, then append migrated legacy rows.
        merged_rows = list(existing)
        merged_rows.extend(migrated)
        _write_events_jsonl(events_fp, merged_rows)

        data.pop("events", None)
        _write_yaml_map(fp, data)

        touched.append(str(fp.relative_to(repo_path)).replace("\\", "/"))
        touched.append(str(events_fp.relative_to(repo_path)).replace("\\", "/"))
        changed = True

    return {"changed": changed, "touched": sorted(set(touched))}


def _needs_build_events_to_jsonl(repo_path: Path) -> bool:
    for fp in _entity_yml_paths(repo_path):
        sfid = fp.parent.name
        if not sfid.startswith("b_"):
            continue
        data = _load_yaml_map(fp)
        if isinstance(data.get("events"), list):
            return True
    return False


def _migration_build_field_rename(repo_path: Path) -> Dict:
    touched: List[str] = []
    changed = False
    for fp in _entity_yml_paths(repo_path):
        sfid = fp.parent.name
        if not sfid.startswith("b_"):
            continue

        data = _load_yaml_map(fp)
        local_changed = False

        part_sfid = str(data.get("part_sfid") or "").strip()
        if not part_sfid:
            for legacy_key in ("product_sfid", "top_part"):
                cand = str(data.get(legacy_key) or "").strip()
                if cand:
                    data["part_sfid"] = cand
                    part_sfid = cand
                    local_changed = True
                    break

        if "part_rev" not in data and data.get("product_rev") not in (None, ""):
            data["part_rev"] = data.get("product_rev")
            local_changed = True

        for legacy_key in ("top_part", "product_sfid", "product_rev"):
            if legacy_key in data:
                data.pop(legacy_key, None)
                local_changed = True

        if local_changed:
            _write_yaml_map(fp, data)
            touched.append(str(fp.relative_to(repo_path)).replace("\\", "/"))
            changed = True

    return {"changed": changed, "touched": sorted(set(touched))}


def _needs_build_field_rename(repo_path: Path) -> bool:
    legacy_keys = {"top_part", "product_sfid", "product_rev"}
    for fp in _entity_yml_paths(repo_path):
        sfid = fp.parent.name
        if not sfid.startswith("b_"):
            continue
        data = _load_yaml_map(fp)
        if any(k in data for k in legacy_keys):
            return True
    return False


def _needs_gitignore_lock_patterns(repo_path: Path) -> bool:
    gi = repo_path / ".gitignore"
    if not gi.exists():
        return True
    try:
        content = gi.read_text(encoding="utf-8")
    except Exception:
        return True
    for pat in _LOCK_GITIGNORE_PATTERNS:
        if pat not in content:
            return True
    if _stale_transient_lock_artifacts(repo_path):
        # stale transient lock files are also a reason to run this migration
        return True
    return False


def _migration_gitignore_lock_patterns(repo_path: Path) -> Dict:
    touched: List[str] = []
    changed = False

    gi = repo_path / ".gitignore"
    content = ""
    if gi.exists():
        try:
            content = gi.read_text(encoding="utf-8")
        except Exception:
            content = ""

    to_add = [pat for pat in _LOCK_GITIGNORE_PATTERNS if pat not in content]
    if to_add:
        with open(gi, "a", encoding="utf-8") as f:
            if content and not content.endswith("\n"):
                f.write("\n")
            f.write("\n# smallFactory transient lock files\n")
            for pat in to_add:
                f.write(pat + "\n")
        touched.append(str(gi.relative_to(repo_path)).replace("\\", "/"))
        changed = True

    removed = _cleanup_transient_lock_artifacts(repo_path)
    if removed:
        touched.extend(removed)
        changed = True

    return {"changed": changed, "touched": sorted(set(touched))}


MIGRATIONS: List[Migration] = [
    Migration(
        id="20250811_entity_file_layout",
        introduced_version="1.0",
        description="Migrate flat entities/<sfid>.yml files to entities/<sfid>/entity.yml.",
        is_needed=_needs_entity_files_flat_to_dir,
        apply=_migration_entity_files_flat_to_dir,
    ),
    Migration(
        id="20250811_design_to_files",
        introduced_version="1.0",
        description="Rename legacy entities/<sfid>/design/ working tree to entities/<sfid>/files/.",
        is_needed=_needs_design_to_files,
        apply=_migration_design_to_files,
    ),
    Migration(
        id="20250814_children_to_bom",
        introduced_version="1.0",
        description="Rename legacy entity.yml key children -> bom.",
        is_needed=_needs_children_to_bom,
        apply=_migration_children_to_bom,
    ),
    Migration(
        id="20260227_build_events_jsonl",
        introduced_version="1.0",
        description="Move build event history from entity.yml events[] into events.jsonl sidecar.",
        is_needed=_needs_build_events_to_jsonl,
        apply=_migration_build_events_to_jsonl,
    ),
    Migration(
        id="20260228_build_part_fields",
        introduced_version="1.0",
        description="Rename build fields top_part/product_sfid/product_rev to part_sfid/part_rev.",
        is_needed=_needs_build_field_rename,
        apply=_migration_build_field_rename,
    ),
    Migration(
        id="20260301_gitignore_lock_patterns",
        introduced_version="1.1",
        description="Ensure lock-file ignore patterns in .gitignore and clean stale transient lock files.",
        is_needed=_needs_gitignore_lock_patterns,
        apply=_migration_gitignore_lock_patterns,
    ),
]


def _resolve_target_migration_id(target: Optional[str]) -> Optional[str]:
    if target in (None, "", "latest"):
        return None
    target_s = str(target).strip()
    known = set(_known_migration_ids())
    if target_s in known:
        return target_s
    raise ValueError(
        f"Unknown migration target '{target_s}'. Use one of: {', '.join(_known_migration_ids())}"
    )


def _planned_migrations(repo_path: Path, applied: List[str], *, target_id: Optional[str]) -> List[Migration]:
    pending: List[Migration] = []
    for m in MIGRATIONS:
        if m.id in applied:
            if target_id and m.id == target_id:
                break
            continue
        if m.is_needed(repo_path):
            pending.append(m)
        if target_id and m.id == target_id:
            break

    if target_id and target_id not in [m.id for m in MIGRATIONS]:
        raise ValueError(f"Unknown target migration id '{target_id}'")
    if target_id and target_id in applied:
        return []
    return pending


def _is_git_repo(repo_path: Path) -> bool:
    r = subprocess.run(
        ["git", "rev-parse", "--is-inside-work-tree"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def _git_dirty(repo_path: Path) -> bool:
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False
    return bool((r.stdout or "").strip())


def _git_commit_upgrade(repo_path: Path, migration_ids: List[str]) -> Dict:
    if not _is_git_repo(repo_path):
        return {"committed": False, "commit": None}

    add = subprocess.run(["git", "add", "-A"], cwd=repo_path, capture_output=True, text=True)
    if add.returncode != 0:
        raise RuntimeError(f"git add failed: {(add.stderr or add.stdout or '').strip()}")

    diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=repo_path)
    if diff.returncode == 0:
        return {"committed": False, "commit": None}

    msg_lines = [
        "[smallFactory] Repo format upgrade",
        "::sf-op::repo-upgrade",
        f"::sf-migrations::{','.join(migration_ids)}",
    ]
    cm = subprocess.run(
        ["git", "commit", "-m", "\n".join(msg_lines)],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    if cm.returncode != 0:
        raise RuntimeError(f"git commit failed: {(cm.stderr or cm.stdout or '').strip()}")

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path, capture_output=True, text=True)
    commit = (head.stdout or "").strip() if head.returncode == 0 else None
    return {"committed": True, "commit": commit}


def get_repo_upgrade_status(repo_path: Path) -> Dict:
    repo_path = repo_path.expanduser().resolve()
    cfg_path = _repo_cfg_path(repo_path)
    cfg = _normalize_repo_metadata(_load_yaml_map(cfg_path))
    applied = _normalize_applied_migrations(cfg.get("applied_migrations"))

    known_ids = set(_known_migration_ids())
    unknown_applied = [mid for mid in applied if mid not in known_ids]
    pending = [m.id for m in MIGRATIONS if m.id not in applied and m.is_needed(repo_path)]
    repo_version = str(cfg.get("smallfactory_version") or SF_TOOL_VERSION)
    version_state = _version_state(repo_version)
    if version_state == "match" and pending:
        version_state = "repo_older"

    return {
        "repo_path": str(repo_path),
        "repo_config": str(cfg_path),
        "tool_version": SF_TOOL_VERSION,
        "repo_version": repo_version,
        "version_state": version_state,
        "known_migrations": _known_migration_ids(),
        "applied_migrations": applied,
        "unknown_applied_migrations": unknown_applied,
        "pending_migrations": pending,
        "upgrade_needed": bool(pending or version_state == "repo_older"),
        "requires_tool_upgrade": bool(version_state == "repo_newer"),
    }


def run_repo_upgrade(
    repo_path: Path,
    *,
    dry_run: bool = False,
    target: Optional[str] = None,
    allow_dirty: bool = False,
    create_commit: bool = True,
    run_validation: bool = True,
) -> Dict:
    repo_path = repo_path.expanduser().resolve()
    cfg_path = _repo_cfg_path(repo_path)
    status = get_repo_upgrade_status(repo_path)

    if status["unknown_applied_migrations"]:
        raise RuntimeError(
            "Repo has migration ids unknown to this tool; please upgrade your tool first: "
            + ", ".join(status["unknown_applied_migrations"])
        )
    if status.get("requires_tool_upgrade"):
        raise RuntimeError(
            f"Repo version {status.get('repo_version')} is newer than tool version {SF_TOOL_VERSION}; "
            "please upgrade the tool first."
        )

    target_id = _resolve_target_migration_id(target)
    applied = list(status["applied_migrations"])
    plan = _planned_migrations(repo_path, applied, target_id=target_id)
    needs_version_bump = status.get("version_state") == "repo_older"

    if dry_run:
        return {
            **status,
            "planned_migrations": [
                {
                    "id": m.id,
                    "introduced_version": m.introduced_version,
                    "description": m.description,
                }
                for m in plan
            ],
            "would_apply": [m.id for m in plan],
            "would_bump_version": bool(needs_version_bump),
            "dry_run": True,
        }

    if (not plan) and (not needs_version_bump):
        return {
            **status,
            "planned_migrations": [],
            "executed_migrations": [],
            "touched_paths": [],
            "validation": None,
            "commit": {"committed": False, "commit": None},
            "dry_run": False,
        }

    with repo_process_lock(repo_path, timeout_seconds=30.0, poll_interval_seconds=0.05):
        with upgrade_in_progress_marker(repo_path):
            _cleanup_transient_lock_artifacts(repo_path)

            if _is_git_repo(repo_path) and (not allow_dirty) and _git_dirty(repo_path):
                raise RuntimeError(
                    "Repository has uncommitted changes. Commit/stash first or use --allow-dirty."
                )

            executed: List[Dict] = []
            touched: List[str] = []
            for mig in plan:
                res = mig.apply(repo_path)
                row = {
                    "id": mig.id,
                    "description": mig.description,
                    "changed": bool(res.get("changed")),
                    "touched": list(res.get("touched") or []),
                }
                executed.append(row)
                touched.extend(row["touched"])

            cfg = _normalize_repo_metadata(_load_yaml_map(cfg_path))
            applied_now = _normalize_applied_migrations(cfg.get("applied_migrations"))
            for mig in plan:
                if mig.id not in applied_now:
                    applied_now.append(mig.id)

            cfg["smallfactory_version"] = SF_TOOL_VERSION
            cfg.pop("compat_version", None)
            cfg["applied_migrations"] = applied_now
            cfg["last_upgraded_by"] = {
                "tool_version": SF_TOOL_VERSION,
                "at": datetime.now(timezone.utc).isoformat(),
                "migration_ids": [m.id for m in plan],
            }
            _write_yaml_map(cfg_path, cfg)
            touched.append(str(cfg_path.relative_to(repo_path)).replace("\\", "/"))

            validation = None
            if run_validation:
                validation = validate_repo(
                    repo_path,
                    include_entities=True,
                    include_inventory=True,
                    include_git=False,
                )
                if int(validation.get("errors", 0) or 0) > 0:
                    raise RuntimeError(
                        f"Validation failed after upgrade: {validation.get('errors')} errors, "
                        f"{validation.get('warnings')} warnings"
                    )

            commit_info = {"committed": False, "commit": None}
            if create_commit and (plan or needs_version_bump):
                commit_info = _git_commit_upgrade(repo_path, [m.id for m in plan])

            return {
                **get_repo_upgrade_status(repo_path),
                "planned_migrations": [m.id for m in plan],
                "executed_migrations": executed,
                "touched_paths": sorted(set(touched)),
                "validation": validation,
                "commit": commit_info,
                "dry_run": False,
            }
