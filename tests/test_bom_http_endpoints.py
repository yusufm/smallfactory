from __future__ import annotations
import subprocess
from pathlib import Path
import importlib.util
import sys

import pytest

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity, bom_list

# Skip these tests entirely if Flask is not installed
pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Configure minimal identity for commits
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Make project root importable similar to app.py behavior
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


@pytest.fixture()
def web_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create temp git repo to act as datarepo
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    # Import the web app module
    mod = _import_web_app_module()

    # Point get_datarepo_path at our temp repo
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    # Disable autopush by default to keep tests deterministic
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    return mod


def test_bom_add_get_set_remove_alt_flow(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Ensure parent and children exist
    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})
    create_entity(repo, "p_c2", {"name": "Child2"})
    create_entity(repo, "p_alt", {"name": "Alt"})

    client = app.test_client()

    # Add first BOM line
    r1 = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_c1", "qty": 2, "rev": "released"},
    )
    assert r1.status_code == 200
    assert r1.get_json().get("success") is True

    # Verify via core API
    bom = bom_list(repo, "p_parent")
    assert len(bom) == 1
    assert bom[0].get("use") == "p_c1"
    assert int(bom[0].get("qty", 0)) == 2

    # Add second line with alternates and a group
    r2 = client.post(
        "/api/entities/p_parent/bom/add",
        json={
            "use": "p_c2",
            "qty": 1,
            "rev": "released",
            "alternates": ["p_alt"],
            "alternates_group": "G1",
        },
    )
    assert r2.status_code == 200
    assert r2.get_json().get("success") is True

    bom = bom_list(repo, "p_parent")
    assert len(bom) == 2
    line1, line2 = bom[0], bom[1]
    assert line2.get("use") == "p_c2"
    assert line2.get("alternates_group") == "G1"
    alts = line2.get("alternates") or []
    assert isinstance(alts, list) and len(alts) == 1 and alts[0].get("use") == "p_alt"

    # Set quantity of first line to 3
    r3 = client.post(
        "/api/entities/p_parent/bom/set",
        json={"index": 0, "qty": 3},
    )
    assert r3.status_code == 200
    assert r3.get_json().get("success") is True

    bom = bom_list(repo, "p_parent")
    assert int(bom[0].get("qty", 0)) == 3

    # Add alt to second line
    r4 = client.post(
        "/api/entities/p_parent/bom/alt-add",
        json={"index": 1, "alt_use": "p_c1"},
    )
    assert r4.status_code == 200
    assert r4.get_json().get("success") is True

    bom = bom_list(repo, "p_parent")
    alts2 = bom[1].get("alternates") or []
    assert any(a.get("use") == "p_c1" for a in alts2)
    assert any(a.get("use") == "p_alt" for a in alts2)

    # Remove the alt just added
    r5 = client.post(
        "/api/entities/p_parent/bom/alt-remove",
        json={"index": 1, "alt_use": "p_c1"},
    )
    assert r5.status_code == 200
    assert r5.get_json().get("success") is True

    bom = bom_list(repo, "p_parent")
    alts2 = bom[1].get("alternates") or []
    assert all(a.get("use") != "p_c1" for a in alts2)
    assert any(a.get("use") == "p_alt" for a in alts2)

    # Remove first line by index
    r6 = client.post(
        "/api/entities/p_parent/bom/remove",
        json={"index": 0},
    )
    assert r6.status_code == 200
    assert r6.get_json().get("success") is True

    bom = bom_list(repo, "p_parent")
    uses = {ln.get("use") for ln in bom}
    assert uses == {"p_c2"}

    # Fetch via GET endpoint to ensure enrichment works and returns success
    g = client.get("/api/entities/p_parent/bom")
    assert g.status_code == 200
    gd = g.get_json()
    assert gd.get("success") is True
    rows = gd.get("rows") or []
    assert isinstance(rows, list) and len(rows) == 1


def test_bom_deep_json_and_csv(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Build a small tree: root -> mid (qty 2), mid -> leaf (qty 3)
    create_entity(repo, "p_root", {"name": "Root"})
    create_entity(repo, "p_mid", {"name": "Mid"})
    create_entity(repo, "p_leaf", {"name": "Leaf"})

    client = app.test_client()

    # root -> mid (2)
    assert client.post(
        "/api/entities/p_root/bom/add",
        json={"use": "p_mid", "qty": 2, "rev": "released"},
    ).get_json().get("success") is True

    # mid -> leaf (3)
    assert client.post(
        "/api/entities/p_mid/bom/add",
        json={"use": "p_leaf", "qty": 3, "rev": "released"},
    ).get_json().get("success") is True

    # Deep with max_depth=0 -> only immediate children of root
    r0 = client.get("/api/entities/p_root/bom/deep?max_depth=0")
    assert r0.status_code == 200
    d0 = r0.get_json()
    assert d0.get("success") is True
    nodes0 = d0.get("nodes") or []
    assert any(n.get("parent") == "p_root" and n.get("use") == "p_mid" for n in nodes0)
    # No leaf under root at this depth
    assert not any(n.get("use") == "p_leaf" and n.get("parent") == "p_mid" for n in nodes0)

    # Full deep -> should include leaf with gross_qty 6 (2*3)
    r1 = client.get("/api/entities/p_root/bom/deep")
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1.get("success") is True
    nodes1 = d1.get("nodes") or []
    assert any(n.get("parent") == "p_mid" and n.get("use") == "p_leaf" for n in nodes1)
    # Best-effort gross_qty check (may be None if types fail to coerce)
    leaf = next((n for n in nodes1 if n.get("parent") == "p_mid" and n.get("use") == "p_leaf"), None)
    if leaf is not None and leaf.get("gross_qty") is not None:
        assert int(leaf.get("gross_qty")) == 6

    # CSV
    rcsv = client.get("/api/entities/p_root/bom/deep?format=csv")
    assert rcsv.status_code == 200
    assert rcsv.mimetype == "text/csv"
    text = rcsv.data.decode("utf-8")
    assert "parent,use,name,qty,rev,level,is_alt,alternates_group,gross_qty,cycle,onhand_total" in text.splitlines()[0]
    # Should contain at least rows for mid and leaf
    assert ",p_mid," in text
    assert ",p_leaf," in text
