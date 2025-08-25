from __future__ import annotations
from pathlib import Path
import sys
import yaml

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import (
    create_entity,
    get_revisions,
    bump_revision,
    release_revision,
    cut_revision,
)


def read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def test_bump_and_release_revision_flow(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Create a part entity
    ent = create_entity(repo, "p_rev", {"name": "Rev Part"})
    assert ent["sfid"] == "p_rev"

    # Initially no released revision
    info0 = get_revisions(repo, "p_rev")
    assert info0["rev"] is None
    assert info0["revisions"] == []

    # Bump (cut) a draft revision
    resp = bump_revision(repo, "p_rev", notes="draft 1")
    assert resp["new_rev"] == "1"

    # Snapshot exists with draft meta and artifacts
    snap_dir = repo / "entities" / "p_rev" / "revisions" / "1"
    meta_fp = snap_dir / "meta.yml"
    assert meta_fp.exists()
    meta = read_yaml(meta_fp)
    assert meta.get("rev") == "1"
    assert meta.get("status") == "draft"
    # Should include entity.yml in artifacts
    arts = meta.get("artifacts") or []
    assert any(a.get("path") == "entity.yml" and a.get("role") == "entity" for a in arts)

    # Now release the draft
    resp2 = release_revision(repo, "p_rev", "1", notes="release 1")
    assert resp2["rev"] == "1"

    # Released pointer updated
    released_fp = repo / "entities" / "p_rev" / "refs" / "released"
    assert released_fp.exists() and released_fp.read_text().strip() == "1"
    # Meta updated to released
    meta2 = read_yaml(meta_fp)
    assert meta2.get("status") == "released"
    assert isinstance(meta2.get("released_at"), str) and meta2["released_at"]

    # Bump again -> new draft '2', pointer remains at 1 until released
    resp3 = bump_revision(repo, "p_rev", notes="draft 2")
    assert resp3["new_rev"] == "2"
    info_after = get_revisions(repo, "p_rev")
    assert info_after["rev"] == "1"

    # Release '2' flips pointer
    release_revision(repo, "p_rev", "2")
    assert (released_fp.read_text().strip()) == "2"


def test_cut_with_custom_and_numeric_sequence(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    create_entity(repo, "p_seq", {"name": "Seq Part"})

    # Cut an alpha label explicitly
    cut_revision(repo, "p_seq", rev="a", notes="alpha tag")
    info = get_revisions(repo, "p_seq")
    ids = [m.get("id") for m in info["revisions"]]
    assert "a" in ids

    # Next bump should start numeric sequence at 1
    r1 = bump_revision(repo, "p_seq")
    assert r1["new_rev"] == "1"

    # Next bump increments numerically
    r2 = bump_revision(repo, "p_seq")
    assert r2["new_rev"] == "2"
