from __future__ import annotations

import subprocess
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import (
    create_entity,
    delete_entity,
    get_entity,
    retire_entity,
    update_entity_field,
    update_entity_fields,
)


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def test_create_part_entity_scaffolds_revision_and_refs_dirs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    created = create_entity(repo, "p_widget", {"name": "Widget"})
    assert created["sfid"] == "p_widget"

    ent_dir = repo / "entities" / "p_widget"
    assert (ent_dir / "entity.yml").exists()
    assert (ent_dir / "revisions" / ".gitkeep").exists()
    assert (ent_dir / "refs" / ".gitkeep").exists()
    # files/ is intentionally lazy-created by file APIs.
    assert not (ent_dir / "files").exists()

    on_disk = yaml.safe_load((ent_dir / "entity.yml").read_text()) or {}
    assert "sfid" not in on_disk


def test_entity_specs_are_enforced_on_create_and_update(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    (repo / "sfdatarepo.yml").write_text(
        (
            "entities:\n"
            "  fields:\n"
            "    name:\n"
            "      required: true\n"
            "  types:\n"
            "    p:\n"
            "      fields:\n"
            "        mpn:\n"
            "          regex: '^[A-Z0-9-]+$'\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing required field: name"):
        create_entity(repo, "p_bad", {"mpn": "ABC-1"})

    create_entity(repo, "p_good", {"name": "Good Part", "mpn": "ABC-1"})

    with pytest.raises(ValueError, match="does not match regex"):
        update_entity_field(repo, "p_good", "mpn", "bad value")

    with pytest.raises(ValueError, match="Cannot update 'sfid'"):
        update_entity_fields(repo, "p_good", {"sfid": "p_other"})

    updated = update_entity_fields(repo, "p_good", {"name": "Renamed Part", "mpn": "XYZ-9"})
    assert updated["name"] == "Renamed Part"
    assert updated["mpn"] == "XYZ-9"

    stored = get_entity(repo, "p_good")
    assert stored["name"] == "Renamed Part"
    assert stored["mpn"] == "XYZ-9"


def test_retire_sets_metadata_and_hard_delete_is_disallowed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    create_entity(repo, "p_legacy", {"name": "Legacy"})

    with pytest.raises(RuntimeError, match="Hard delete of entities is disabled"):
        delete_entity(repo, "p_legacy")

    retired = retire_entity(
        repo,
        "p_legacy",
        reason="replaced by p_new",
        retired_at="2026-01-01T00:00:00Z",
    )
    assert retired["retired"] is True
    assert retired["retired_at"] == "2026-01-01T00:00:00Z"
    assert retired["retired_reason"] == "replaced by p_new"

    stored = get_entity(repo, "p_legacy")
    assert stored["retired"] is True
    assert stored["retired_at"] == "2026-01-01T00:00:00Z"
    assert stored["retired_reason"] == "replaced by p_new"
