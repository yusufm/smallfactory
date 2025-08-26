from __future__ import annotations
from pathlib import Path
import sys
import yaml

# Ensure package importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import (
    create_entity,
    bom_add_line,
    bom_set_line,
    resolved_bom_tree,
    cut_revision,
)


def _read_yaml(p: Path) -> dict:
    with open(p) as f:
        return yaml.safe_load(f) or {}


def test_resolved_bom_tree_honors_child_rev_for_bom_loading(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Leaves
    create_entity(repo, "p_leaf_a", {"name": "Leaf A"})
    create_entity(repo, "p_leaf_b", {"name": "Leaf B"})

    # Mid assembly: rev 1 -> leaf_a; rev 2 -> leaf_b
    create_entity(repo, "p_mid", {"name": "Mid"})
    bom_add_line(repo, "p_mid", use="p_leaf_a", qty=1, rev="released")
    cut_revision(repo, "p_mid", rev="1")

    # Change BOM to leaf_b and cut rev 2
    # Replace the only line's use to p_leaf_b
    bom_set_line(repo, "p_mid", index=0, updates={"use": "p_leaf_b"})
    cut_revision(repo, "p_mid", rev="2")

    # Top assembly pointing to p_mid at a specific revision
    create_entity(repo, "p_top", {"name": "Top"})
    bom_add_line(repo, "p_top", use="p_mid", qty=1, rev="1")

    nodes = resolved_bom_tree(repo, "p_top")

    uses = [n.get("use") for n in nodes]
    # Should include the mid and traverse into its rev-1 BOM → leaf_a
    assert "p_mid" in uses
    assert "p_leaf_a" in uses
    assert "p_leaf_b" not in uses

    # Now point top to mid rev 2 and verify it resolves leaf_b instead
    bom_set_line(repo, "p_top", index=0, updates={"rev": "2"})
    nodes2 = resolved_bom_tree(repo, "p_top")
    uses2 = [n.get("use") for n in nodes2]
    assert "p_leaf_b" in uses2


def test_cut_revision_generates_bom_tree_using_snapshot_and_per_line_revs(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Leaves
    create_entity(repo, "p_la", {"name": "LA"})
    create_entity(repo, "p_lb", {"name": "LB"})

    # Mid assembly snapshots with differing children
    create_entity(repo, "p_mid2", {"name": "Mid2"})
    bom_add_line(repo, "p_mid2", use="p_la", qty=1)
    cut_revision(repo, "p_mid2", rev="1")
    bom_set_line(repo, "p_mid2", index=0, updates={"use": "p_lb"})
    cut_revision(repo, "p_mid2", rev="2")

    # Top points to mid2 at rev 1; snapshot the top and ensure bom_tree.yml captures leaf from rev1
    create_entity(repo, "p_top_snap", {"name": "TopSnap"})
    bom_add_line(repo, "p_top_snap", use="p_mid2", qty=1, rev="1")

    snap = cut_revision(repo, "p_top_snap", rev="A")
    # cut_revision returns current released pointer in 'rev' (None until release).
    # Ensure the newly cut snapshot 'A' exists in the revisions list and is draft.
    labels = [m.get("id") for m in snap.get("revisions", [])]
    assert "A" in labels
    mA = next((m for m in snap.get("revisions", []) if m.get("id") == "A"), None)
    assert mA is not None and mA.get("status") == "draft"

    bom_tree_fp = repo / "entities" / "p_top_snap" / "revisions" / "A" / "bom_tree.yml"
    assert bom_tree_fp.exists()
    doc = _read_yaml(bom_tree_fp)
    assert doc.get("format") == "bom_tree.v1"
    assert doc.get("root") == "p_top_snap"
    assert doc.get("rev") == "A"

    nodes = doc.get("nodes") or []
    uses = [n.get("use") for n in nodes]
    # Snapshot should reflect mid2 rev1 → includes p_la, not p_lb
    assert "p_mid2" in uses
    assert "p_la" in uses
    assert "p_lb" not in uses
