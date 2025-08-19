from __future__ import annotations
import os
import subprocess
from pathlib import Path
import importlib.util

import pytest

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity, get_entity, bom_list


# Skip these tests entirely if Flask is not installed
pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Configure minimal identity for commits
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_has_commit(root: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _git_last_commit_author(root: Path) -> tuple[str, str]:
    name = subprocess.run(["git", "log", "-n", "1", "--pretty=%an"], cwd=root, capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "log", "-n", "1", "--pretty=%ae"], cwd=root, capture_output=True, text=True).stdout.strip()
    return name, email


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
    # Enable autocommit by default
    monkeypatch.setenv("SF_WEB_AUTOCOMMIT", "1")

    return mod


def test_bom_import_apply_adds_creates_and_commits_with_identity(web_mod, tmp_path: Path):
    mod = web_mod
    app = mod.app

    repo = mod.get_datarepo_path()
    assert isinstance(repo, Path)

    # Ensure parent and an existing child entity
    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_exist", {"name": "Existing"})

    client = app.test_client()

    payload = {
        "rows": [
            {
                "use": "p_child1",
                "qty": 2,
                "rev": "released",
                "name": "Cap 10uF",
                "manufacturer": "Murata",
                "mpn": "GRM188R60J106ME47",
            },
            {"use": "p_exist", "qty": 1, "rev": "released"},
            {"use": "", "qty": 5},  # invalid row ignored
            {"use": "p_skip", "qty": 1, "ambiguous": True},  # ambiguous row ignored
        ]
    }

    headers = {
        "X-Forwarded-User": "Jane Doe",
        "X-Forwarded-Email": "jane.doe@example.com",
    }

    resp = client.post(
        "/api/entities/p_parent/bom/import/apply",
        json=payload,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is True

    # Summary checks
    summary = data.get("summary") or {}
    assert summary.get("added") == 2
    assert summary.get("created") == 1
    # Created entity list sanity
    created_entities = data.get("created_entities") or []
    assert any(e.get("sfid") == "p_child1" for e in created_entities)

    # Entity was created with provided fields and attrs packing
    child = get_entity(repo, "p_child1")
    assert child.get("name") == "Cap 10uF"
    # Manufacturer/MPN should be preserved (likely under attrs per app logic)
    # Accept either top-level or under attrs to be future-proof
    if "manufacturer" in child or "mpn" in child:
        assert child.get("manufacturer", "") or child.get("mpn", "")
    else:
        attrs = child.get("attrs") or {}
        assert attrs.get("manufacturer") == "Murata"
        assert attrs.get("mpn") == "GRM188R60J106ME47"

    # BOM now contains the two uses
    bom = bom_list(repo, "p_parent")
    uses = {line.get("use") for line in bom}
    assert {"p_child1", "p_exist"}.issubset(uses)

    # A commit was created with the proxied identity
    assert _git_has_commit(repo)
    an, ae = _git_last_commit_author(repo)
    assert an == "Jane Doe"
    assert ae == "jane.doe@example.com"


def test_bom_import_apply_remove_missing_flag(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_parent2", {"name": "Parent2"})
    create_entity(repo, "p_a", {"name": "A"})
    create_entity(repo, "p_b", {"name": "B"})

    client = app.test_client()

    # Initial add both A and B
    resp1 = client.post(
        "/api/entities/p_parent2/bom/import/apply",
        json={"rows": [{"use": "p_a", "qty": 1}, {"use": "p_b", "qty": 1}]},
    )
    assert resp1.get_json().get("success") is True

    # Now apply only A, with remove_missing=False => B should remain
    resp2 = client.post(
        "/api/entities/p_parent2/bom/import/apply",
        json={"rows": [{"use": "p_a", "qty": 1}], "remove_missing": False},
    )
    data2 = resp2.get_json()
    assert data2.get("success") is True
    assert data2["summary"].get("removed") == 0

    bom = bom_list(repo, "p_parent2")
    uses = {line.get("use") for line in bom}
    assert {"p_a", "p_b"}.issubset(uses)


def test_bom_import_apply_update_existing_toggle(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_parent3", {"name": "Parent3"})
    create_entity(repo, "p_x", {"name": "X"})

    client = app.test_client()

    # Add X with qty 1
    r1 = client.post(
        "/api/entities/p_parent3/bom/import/apply",
        json={"rows": [{"use": "p_x", "qty": 1}]},
    )
    assert r1.get_json().get("success") is True

    # Attempt to change qty to 3 with update_existing=False
    r2 = client.post(
        "/api/entities/p_parent3/bom/import/apply",
        json={"rows": [{"use": "p_x", "qty": 3}], "update_existing": False},
    )
    d2 = r2.get_json()
    assert d2.get("success") is True
    assert d2["summary"].get("updated") == 0
    # BOM should still show qty 1
    bom = bom_list(repo, "p_parent3")
    line = next((ln for ln in bom if ln.get("use") == "p_x"), None)
    assert line and int(line.get("qty", 0)) == 1

    # Now allow updates; qty should update to 3
    r3 = client.post(
        "/api/entities/p_parent3/bom/import/apply",
        json={"rows": [{"use": "p_x", "qty": 3}], "update_existing": True},
    )
    d3 = r3.get_json()
    assert d3.get("success") is True
    assert d3["summary"].get("updated") == 1
    bom2 = bom_list(repo, "p_parent3")
    line2 = next((ln for ln in bom2 if ln.get("use") == "p_x"), None)
    assert line2 and int(line2.get("qty", 0)) == 3


def test_run_repo_txn_autopush_async_triggers_spawn(monkeypatch: pytest.MonkeyPatch, web_mod, tmp_path: Path):
    mod = web_mod
    repo = mod.get_datarepo_path()

    # Enable autopush (async) with TTL=0 so immediate async push is requested
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "1")
    monkeypatch.setenv("SF_WEB_AUTOPUSH_ASYNC", "1")
    monkeypatch.setenv("SF_GIT_PUSH_TTL_SEC", "0")
    monkeypatch.setenv("SF_WEB_AUTOCOMMIT", "1")

    called = {"n": 0}

    def fake_spawn(path):
        called["n"] += 1

    monkeypatch.setattr(mod, "_spawn_async_push", fake_spawn)

    target = repo / "entities" / "p_async" / "entity.yml"
    target.parent.mkdir(parents=True, exist_ok=True)

    def mutate():
        target.write_text("name: Async Test\n")
        return {"ok": True}

    res = mod._run_repo_txn(repo, mutate, autocommit_message="[test] async", autocommit_paths=["entities/p_async"])
    assert res["ok"] is True
    # Should have requested an async push exactly once
    assert called["n"] == 1


def test_run_repo_txn_pull_failure_raises(monkeypatch: pytest.MonkeyPatch, web_mod, tmp_path: Path):
    mod = web_mod
    repo = mod.get_datarepo_path()

    # Ensure git is enabled and autocommit on
    monkeypatch.setenv("SF_GIT_DISABLED", "0")
    monkeypatch.setenv("SF_WEB_AUTOCOMMIT", "1")

    # Force _safe_git_pull to fail
    monkeypatch.setattr(mod, "_safe_git_pull", lambda p: (False, "boom"))

    with pytest.raises(RuntimeError):
        mod._run_repo_txn(repo, lambda: {"ok": True}, autocommit_message="x", autocommit_paths=["."])


def test_bom_preview_passes_extra_fields_and_apply_persists_attrs(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Ensure parent exists
    create_entity(repo, "p_parentx", {"name": "ParentX"})

    client = app.test_client()

    # Build CSV with extra fields beyond canonical ones
    csv_text = """use,qty,rev,manufacturer,mpn,voltage,tolerance,name
p_caps,2,released,Murata,GRM188R60J106ME47,10V,5%,Cap 10uF
"""

    # Call preview via JSON payload
    pr = client.post(
        "/api/entities/p_parentx/bom/import/preview",
        json={"csv_text": csv_text},
    )
    assert pr.status_code == 200
    pdata = pr.get_json()
    assert pdata.get("success") is True
    rows = pdata.get("rows") or []
    assert len(rows) == 1
    r0 = rows[0]
    # Preview should have passed through extra fields
    assert r0.get("voltage") == "10V"
    assert r0.get("tolerance") == "5%"

    # Now apply using the preview rows; this should create the child entity
    ar = client.post(
        "/api/entities/p_parentx/bom/import/apply",
        json={"rows": rows},
    )
    assert ar.status_code == 200
    adata = ar.get_json()
    assert adata.get("success") is True

    # Verify entity was created and attributes persisted under attrs
    child = get_entity(repo, "p_caps")
    assert child.get("name") == "Cap 10uF"
    attrs = child.get("attrs") or {}
    assert attrs.get("manufacturer") == "Murata"
    assert attrs.get("mpn") == "GRM188R60J106ME47"
    assert attrs.get("voltage") == "10V"
    assert attrs.get("tolerance") == "5%"
    # Ensure qty/quantity are NOT persisted as part attributes or top-level fields
    assert "qty" not in attrs
    assert "quantity" not in attrs
    assert "qty" not in child


def test_bom_apply_excludes_quantity_alias_from_attrs(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Ensure parent exists
    create_entity(repo, "p_parenty", {"name": "ParentY"})

    client = app.test_client()

    # CSV using 'quantity' alias instead of 'qty'
    csv_text = """use,quantity,rev,name,manufacturer,mpn\n
p_qtyalias,7,released,AliasPart,Acme,AC-123\n
"""

    # Preview should canonicalize quantity -> qty and pass through extra fields
    pr = client.post(
        "/api/entities/p_parenty/bom/import/preview",
        json={"csv_text": csv_text},
    )
    assert pr.status_code == 200
    pdata = pr.get_json()
    assert pdata.get("success") is True
    rows = pdata.get("rows") or []
    assert len(rows) == 1
    r0 = rows[0]
    assert str(r0.get("qty")) == "7"

    # Apply using preview rows
    ar = client.post(
        "/api/entities/p_parenty/bom/import/apply",
        json={"rows": rows},
    )
    assert ar.status_code == 200
    adata = ar.get_json()
    assert adata.get("success") is True

    # Child created; qty should be on BOM, not in attrs
    child = get_entity(repo, "p_qtyalias")
    assert child.get("name") == "AliasPart"
    attrs = child.get("attrs") or {}
    assert "qty" not in attrs
    assert "quantity" not in attrs
    assert "qty" not in child

    # BOM line should reflect qty 7
    bom = bom_list(repo, "p_parenty")
    line = next((ln for ln in bom if ln.get("use") == "p_qtyalias"), None)
    assert line and int(line.get("qty", 0)) == 7
