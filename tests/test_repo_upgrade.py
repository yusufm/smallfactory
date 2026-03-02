from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import init_git_repo
from smallfactory.core.v1.config import DATAREPO_CONFIG_FILENAME
from smallfactory.core.v1.repo_upgrade import MIGRATIONS, get_repo_upgrade_status, run_repo_upgrade


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def test_status_reports_pending_migrations_when_not_recorded(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")

    st = get_repo_upgrade_status(repo)
    assert st["repo_version"] == "1.0"
    assert st["pending_migrations"] == ["20260301_gitignore_lock_patterns"]
    assert st["unknown_applied_migrations"] == []


def test_upgrade_migrates_flat_entities_design_children_events_and_build_fields(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)

    _write(
        repo / DATAREPO_CONFIG_FILENAME,
        (
            "smallfactory_version: 1.0\n"
            "inventory:\n"
            "  fields:\n"
            "    quantity:\n"
            "      required: true\n"
            "      regex: '^[0-9]+$'\n"
        ),
    )

    # Legacy flat entity file.
    _write(repo / "entities" / "p_flat.yml", "name: Flat Part\n")

    # Legacy children key + legacy design/ directory.
    _write(
        repo / "entities" / "p_bom" / "entity.yml",
        "name: Assy\nchildren:\n  - use: p_flat\n    qty: 2\n",
    )
    _write(repo / "entities" / "p_bom" / "design" / "drawings" / "assy.pdf", "PDF")

    # Build with legacy events in entity.yml and legacy field names.
    _write(
        repo / "entities" / "b_build1" / "entity.yml",
        (
            "name: Build 1\n"
            "top_part: p_bom\n"
            "product_rev: A\n"
            "events:\n"
            "  - id: evt_old_1\n"
            "    tags: [Repair]\n"
            "    message: Legacy event\n"
        ),
    )

    out = run_repo_upgrade(
        repo,
        create_commit=False,
        run_validation=False,
        allow_dirty=True,
    )

    # Flat file migrated to canonical directory layout.
    assert not (repo / "entities" / "p_flat.yml").exists()
    assert (repo / "entities" / "p_flat" / "entity.yml").exists()

    # children -> bom migrated.
    p_bom = yaml.safe_load((repo / "entities" / "p_bom" / "entity.yml").read_text(encoding="utf-8"))
    assert "children" not in p_bom
    assert isinstance(p_bom.get("bom"), list)
    assert p_bom["bom"][0]["use"] == "p_flat"

    # design/ -> files/ migrated.
    assert not (repo / "entities" / "p_bom" / "design").exists()
    assert (repo / "entities" / "p_bom" / "files" / "drawings" / "assy.pdf").exists()

    # Build fields migrated and events moved to sidecar.
    b1 = yaml.safe_load((repo / "entities" / "b_build1" / "entity.yml").read_text(encoding="utf-8"))
    assert b1.get("part_sfid") == "p_bom"
    assert b1.get("part_rev") == "A"
    assert "top_part" not in b1
    assert "product_rev" not in b1
    assert "events" not in b1

    events_fp = repo / "entities" / "b_build1" / "events.jsonl"
    lines = [ln for ln in events_fp.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    event = json.loads(lines[0])
    assert event["id"] == "evt_old_1"
    assert event["tags"] == ["repair"]

    # Metadata updated with ordered migrations.
    cfg = yaml.safe_load((repo / DATAREPO_CONFIG_FILENAME).read_text(encoding="utf-8"))
    assert cfg["applied_migrations"] == [m.id for m in MIGRATIONS]
    assert cfg["smallfactory_version"] == "1.1"
    assert out["planned_migrations"] == [m.id for m in MIGRATIONS]


def test_upgrade_fails_if_repo_has_unknown_applied_migration(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(
        repo / DATAREPO_CONFIG_FILENAME,
        (
            "smallfactory_version: 1.0\n"
            "applied_migrations:\n"
            "  - 99999999_future\n"
        ),
    )

    with pytest.raises(RuntimeError, match="unknown to this tool"):
        run_repo_upgrade(repo, create_commit=False, run_validation=False, allow_dirty=True)


def test_upgrade_dry_run_does_not_modify_repo(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")
    # Add one legacy marker so dry-run has an actionable plan.
    _write(repo / "entities" / "p_old.yml", "name: old\n")

    res = run_repo_upgrade(repo, dry_run=True)
    assert res["dry_run"] is True
    assert res["would_apply"] == [
        "20250811_entity_file_layout",
        "20260301_gitignore_lock_patterns",
    ]

    # Still no metadata written by dry-run.
    cfg = yaml.safe_load((repo / DATAREPO_CONFIG_FILENAME).read_text(encoding="utf-8")) or {}
    assert "applied_migrations" not in cfg


def test_upgrade_fails_when_upgrade_marker_exists(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")
    _write(repo / ".git" / ".smallfactory.upgrade.in_progress", "busy\n")

    with pytest.raises(RuntimeError, match="already in progress"):
        run_repo_upgrade(repo, create_commit=False, run_validation=False, allow_dirty=True)


def test_upgrade_runs_validation_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")

    calls = []

    def _fake_validate(repo_path, *, include_entities, include_inventory, include_git):
        calls.append((repo_path, include_entities, include_inventory, include_git))
        return {"errors": 0, "warnings": 0}

    monkeypatch.setattr("smallfactory.core.v1.repo_upgrade.validate_repo", _fake_validate)

    run_repo_upgrade(repo, create_commit=False, allow_dirty=True)
    assert len(calls) == 1
    assert calls[0][0] == repo.resolve()
    assert calls[0][1:] == (True, True, False)


def test_upgrade_fails_on_post_validation_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")

    def _fake_validate(*_args, **_kwargs):
        return {"errors": 1, "warnings": 0}

    monkeypatch.setattr("smallfactory.core.v1.repo_upgrade.validate_repo", _fake_validate)

    with pytest.raises(RuntimeError, match="Validation failed after upgrade"):
        run_repo_upgrade(repo, create_commit=False, allow_dirty=True)


def test_upgrade_cleans_transient_locks_before_dirty_check(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    _write(repo / DATAREPO_CONFIG_FILENAME, "smallfactory_version: 1.0\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo, check=True)

    _write(repo / ".smallfactory.repo.lock.12345", "lock\n")
    _write(repo / "inventory" / "lots" / "abc.lock", "lock\n")

    out = run_repo_upgrade(repo, create_commit=False, run_validation=False, allow_dirty=False)
    assert "20260301_gitignore_lock_patterns" in out["planned_migrations"]
    assert not (repo / ".smallfactory.repo.lock.12345").exists()
    assert not (repo / "inventory" / "lots" / "abc.lock").exists()
