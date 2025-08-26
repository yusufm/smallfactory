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
    release_revision,
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


def test_resolved_bom_tree_honors_per_line_rev_for_alternates(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Leaves for asm1 and asm2 across two revisions
    create_entity(repo, "p_ax", {"name": "AX"})
    create_entity(repo, "p_ay", {"name": "AY"})
    create_entity(repo, "p_bx", {"name": "BX"})
    create_entity(repo, "p_by", {"name": "BY"})

    # Assembly 1: rev1 -> ax, rev2 -> ay
    create_entity(repo, "p_asm1", {"name": "ASM1"})
    bom_add_line(repo, "p_asm1", use="p_ax", qty=1)
    cut_revision(repo, "p_asm1", rev="1")
    bom_set_line(repo, "p_asm1", index=0, updates={"use": "p_ay"})
    cut_revision(repo, "p_asm1", rev="2")

    # Assembly 2: rev1 -> bx, rev2 -> by
    create_entity(repo, "p_asm2", {"name": "ASM2"})
    bom_add_line(repo, "p_asm2", use="p_bx", qty=1)
    cut_revision(repo, "p_asm2", rev="1")
    bom_set_line(repo, "p_asm2", index=0, updates={"use": "p_by"})
    cut_revision(repo, "p_asm2", rev="2")

    # Top with a line pointing to asm1 at rev1 and an alternate asm2 (inherits same rev_spec)
    create_entity(repo, "p_top_alt", {"name": "TopAlt"})
    bom_add_line(
        repo,
        "p_top_alt",
        use="p_asm1",
        qty=1,
        rev="1",
        alternates=[{"use": "p_asm2"}],
        alternates_group="G",
    )

    nodes = resolved_bom_tree(repo, "p_top_alt")
    uses = [n.get("use") for n in nodes]
    assert "p_asm1" in uses
    assert "p_ax" in uses  # from asm1 rev1
    assert "p_asm2" in uses  # alternate node should be present
    assert "p_bx" in uses  # from asm2 rev1 (inherits rev=1 from line)
    assert "p_ay" not in uses
    assert "p_by" not in uses

    # Ensure the alternate node is flagged and resolved at rev=1
    alt_nodes = [n for n in nodes if n.get("use") == "p_asm2" and n.get("is_alt") is True]
    assert len(alt_nodes) == 1
    assert alt_nodes[0].get("rev") == "1"

    # Now flip the per-line rev to 2 and verify alternates follow
    bom_set_line(repo, "p_top_alt", index=0, updates={"rev": "2"})
    nodes2 = resolved_bom_tree(repo, "p_top_alt")
    uses2 = [n.get("use") for n in nodes2]
    assert "p_ay" in uses2  # asm1 rev2
    assert "p_by" in uses2  # asm2 rev2 (alternate)
    assert "p_ax" not in uses2
    assert "p_bx" not in uses2


def test_cut_revision_bom_tree_honors_per_line_rev_for_alternates(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Leaves
    create_entity(repo, "p_cx", {"name": "CX"})
    create_entity(repo, "p_cy", {"name": "CY"})
    create_entity(repo, "p_dx", {"name": "DX"})
    create_entity(repo, "p_dy", {"name": "DY"})

    # Assemblies with two snapshots each
    create_entity(repo, "p_asmc", {"name": "ASMC"})
    bom_add_line(repo, "p_asmc", use="p_cx", qty=1)
    cut_revision(repo, "p_asmc", rev="1")
    bom_set_line(repo, "p_asmc", index=0, updates={"use": "p_cy"})
    cut_revision(repo, "p_asmc", rev="2")

    create_entity(repo, "p_asmd", {"name": "ASMD"})
    bom_add_line(repo, "p_asmd", use="p_dx", qty=1)
    cut_revision(repo, "p_asmd", rev="1")
    bom_set_line(repo, "p_asmd", index=0, updates={"use": "p_dy"})
    cut_revision(repo, "p_asmd", rev="2")

    # Top with per-line rev 1 and an alternate
    create_entity(repo, "p_top_alt_snap", {"name": "TopAltSnap"})
    bom_add_line(
        repo,
        "p_top_alt_snap",
        use="p_asmc",
        qty=1,
        rev="1",
        alternates=[{"use": "p_asmd"}],
        alternates_group="G",
    )

    # Snapshot S: should include cx and dx
    resS = cut_revision(repo, "p_top_alt_snap", rev="S")
    labels = [m.get("id") for m in resS.get("revisions", [])]
    assert "S" in labels
    fpS = repo / "entities" / "p_top_alt_snap" / "revisions" / "S" / "bom_tree.yml"
    docS = _read_yaml(fpS)
    nodesS = docS.get("nodes") or []
    usesS = [n.get("use") for n in nodesS]
    assert "p_asmc" in usesS and "p_cx" in usesS
    assert "p_asmd" in usesS and "p_dx" in usesS
    assert "p_cy" not in usesS and "p_dy" not in usesS

    # Change per-line rev to 2 and snapshot T: should include cy and dy
    bom_set_line(repo, "p_top_alt_snap", index=0, updates={"rev": "2"})
    resT = cut_revision(repo, "p_top_alt_snap", rev="T")
    labelsT = [m.get("id") for m in resT.get("revisions", [])]
    assert "T" in labelsT
    fpT = repo / "entities" / "p_top_alt_snap" / "revisions" / "T" / "bom_tree.yml"
    docT = _read_yaml(fpT)
    nodesT = docT.get("nodes") or []
    usesT = [n.get("use") for n in nodesT]
    assert "p_asmc" in usesT and "p_cy" in usesT
    assert "p_asmd" in usesT and "p_dy" in usesT
    assert "p_cx" not in usesT and "p_dx" not in usesT


def test_released_rev_behavior_for_alternates_in_resolved_tree(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Leaves
    create_entity(repo, "p_l1a", {"name": "L1A"})
    create_entity(repo, "p_l1b", {"name": "L1B"})
    create_entity(repo, "p_l2a", {"name": "L2A"})
    create_entity(repo, "p_l2b", {"name": "L2B"})

    # Assemblies with rev1/rev2 and released pointer flips
    create_entity(repo, "p_a1", {"name": "A1"})
    bom_add_line(repo, "p_a1", use="p_l1a", qty=1)
    cut_revision(repo, "p_a1", rev="1"); release_revision(repo, "p_a1", "1")
    bom_set_line(repo, "p_a1", index=0, updates={"use": "p_l1b"})
    cut_revision(repo, "p_a1", rev="2")

    create_entity(repo, "p_a2", {"name": "A2"})
    bom_add_line(repo, "p_a2", use="p_l2a", qty=1)
    cut_revision(repo, "p_a2", rev="1"); release_revision(repo, "p_a2", "1")
    bom_set_line(repo, "p_a2", index=0, updates={"use": "p_l2b"})
    cut_revision(repo, "p_a2", rev="2")

    # Top points to released; alternates inherit 'released'
    create_entity(repo, "p_top_rel", {"name": "TopRel"})
    bom_add_line(
        repo,
        "p_top_rel",
        use="p_a1",
        qty=1,
        rev="released",
        alternates=[{"use": "p_a2"}],
        alternates_group="G",
    )

    nodes1 = resolved_bom_tree(repo, "p_top_rel")
    uses1 = [n.get("use") for n in nodes1]
    assert "p_l1a" in uses1 and "p_l2a" in uses1
    assert "p_l1b" not in uses1 and "p_l2b" not in uses1

    # Flip released pointers to rev2
    release_revision(repo, "p_a1", "2")
    release_revision(repo, "p_a2", "2")
    nodes2 = resolved_bom_tree(repo, "p_top_rel")
    uses2 = [n.get("use") for n in nodes2]
    assert "p_l1b" in uses2 and "p_l2b" in uses2
    assert "p_l1a" not in uses2 and "p_l2a" not in uses2


def test_cycle_flag_set_for_alternate_cycle(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    # Simple cycle introduced via alternate back to parent
    create_entity(repo, "p_ca", {"name": "CA"})
    create_entity(repo, "p_cb", {"name": "CB"})
    bom_add_line(
        repo,
        "p_ca",
        use="p_cb",
        qty=1,
        rev="released",
        alternates=[{"use": "p_ca"}],
        alternates_group="C",
    )

    nodes = resolved_bom_tree(repo, "p_ca")
    # Find the alternate node pointing back to p_ca and ensure cycle flag is set
    alt_cycle = [n for n in nodes if n.get("use") == "p_ca" and n.get("is_alt") and n.get("cycle")]
    assert len(alt_cycle) == 1


def test_snapshot_includes_alt_flags_and_group(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()

    create_entity(repo, "p_m1", {"name": "M1"})
    create_entity(repo, "p_c1", {"name": "C1"})
    create_entity(repo, "p_m2", {"name": "M2"})
    create_entity(repo, "p_c2", {"name": "C2"})

    # m1->c1, m2->c2
    bom_add_line(repo, "p_m1", use="p_c1", qty=2)
    cut_revision(repo, "p_m1", rev="1")
    bom_add_line(repo, "p_m2", use="p_c2", qty=2)
    cut_revision(repo, "p_m2", rev="1")

    create_entity(repo, "p_root_flags", {"name": "RootFlags"})
    bom_add_line(
        repo,
        "p_root_flags",
        use="p_m1",
        qty=3,
        rev="1",
        alternates=[{"use": "p_m2"}],
        alternates_group="G1",
    )

    cut_revision(repo, "p_root_flags", rev="X")
    fp = repo / "entities" / "p_root_flags" / "revisions" / "X" / "bom_tree.yml"
    doc = _read_yaml(fp)
    nodes = doc.get("nodes") or []
    main = next(n for n in nodes if n.get("use") == "p_m1" and not n.get("is_alt"))
    alt = next(n for n in nodes if n.get("use") == "p_m2" and n.get("is_alt"))
    assert main.get("alternates_group") == "G1"
    assert alt.get("alternates_group") == "G1"
    assert alt.get("is_alt") is True
    # qty and gross_qty should carry through consistently for main and alt
    assert main.get("qty") == 3 and alt.get("qty") == 3
    assert main.get("gross_qty") == 3 and alt.get("gross_qty") == 3
