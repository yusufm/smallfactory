from __future__ import annotations
import subprocess
from pathlib import Path
import importlib.util
import sys

import pytest

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity, bom_list, cut_revision

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def _git_head(repo: Path) -> str:
    r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True)
    return (r.stdout or "").strip()


def _git_status_clean(repo: Path) -> bool:
    r = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
    return (r.stdout or "").strip() == ""


@pytest.fixture()
def web_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)
    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    return mod


def test_get_endpoints_are_pure_no_git_changes(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Setup some data
    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    client = app.test_client()
    # Create one BOM line via API (commit occurs)
    assert client.post(
        "/api/entities/p_parent/bom/add", json={"use": "p_child", "qty": 1, "rev": "released"}
    ).get_json().get("success") is True

    head_before = _git_head(repo)
    assert _git_status_clean(repo)

    # Pure reads
    r1 = client.get("/api/entities/p_parent/bom")
    assert r1.status_code == 200 and r1.get_json().get("success") is True

    r2 = client.get("/api/entities/p_parent/bom/deep")
    assert r2.status_code == 200 and r2.get_json().get("success") is True

    r3 = client.get("/api/entities/p_parent/revisions")
    # Revisions API should also be pure
    assert r3.status_code == 200 and r3.get_json().get("success") is True

    head_after = _git_head(repo)
    assert head_after == head_before
    assert _git_status_clean(repo)


def test_post_routes_delegate_to_core_and_fail_without_mutation_on_core_error(web_mod, monkeypatch: pytest.MonkeyPatch):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Seed entities and one BOM line
    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})
    client = app.test_client()
    assert client.post(
        "/api/entities/p_parent/bom/add", json={"use": "p_c1", "qty": 1, "rev": "released"}
    ).get_json().get("success") is True

    def _capture_state():
        return _git_head(repo), list(bom_list(repo, "p_parent"))

    # 1) bom/add
    head0, bom0 = _capture_state()
    with monkeypatch.context() as mp:
        mp.setattr(mod, "bom_add_line", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-add")))
        r = client.post("/api/entities/p_parent/bom/add", json={"use": "p_c1", "qty": 2, "rev": "released"})
        assert r.status_code == 400
        assert _capture_state() == (head0, bom0)

    # 2) bom/set
    head1, bom1 = _capture_state()
    with monkeypatch.context() as mp:
        mp.setattr(mod, "bom_set_line", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-set")))
        r = client.post("/api/entities/p_parent/bom/set", json={"index": 0, "qty": 3})
        assert r.status_code == 400
        assert _capture_state() == (head1, bom1)

    # 3) bom/remove
    head2, bom2 = _capture_state()
    with monkeypatch.context() as mp:
        mp.setattr(mod, "bom_remove_line", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-remove")))
        r = client.post("/api/entities/p_parent/bom/remove", json={"index": 0})
        assert r.status_code == 400
        assert _capture_state() == (head2, bom2)

    # Prepare for alt tests: re-add a line
    if not any((ln or {}).get("use") == "p_c1" for ln in (bom_list(repo, "p_parent") or [])):
        assert client.post(
            "/api/entities/p_parent/bom/add", json={"use": "p_c1", "qty": 1, "rev": "released"}
        ).get_json().get("success") is True

    # 4) bom/alt-add
    head3, bom3 = _capture_state()
    with monkeypatch.context() as mp:
        mp.setattr(mod, "bom_alt_add", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-alt-add")))
        r = client.post("/api/entities/p_parent/bom/alt-add", json={"index": 0, "alt_use": "p_c1"})
        assert r.status_code == 400
        assert _capture_state() == (head3, bom3)

    # Add a valid alternate to enable alt-remove test
    create_entity(repo, "p_alt", {"name": "Alt"})
    assert client.post(
        "/api/entities/p_parent/bom/alt-add", json={"index": 0, "alt_use": "p_alt"}
    ).get_json().get("success") is True

    # 5) bom/alt-remove
    head4, bom4 = _capture_state()
    with monkeypatch.context() as mp:
        mp.setattr(mod, "bom_alt_remove", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-alt-remove")))
        r = client.post("/api/entities/p_parent/bom/alt-remove", json={"index": 0, "alt_use": "p_alt"})
        assert r.status_code == 400
        assert _capture_state() == (head4, bom4)


def test_revision_download_delegates_to_core_and_is_read_only_on_error(web_mod, monkeypatch: pytest.MonkeyPatch):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_revdl", {"name": "RevDL"})
    cut_revision(repo, "p_revdl", rev="1")

    client = app.test_client()
    head_before = _git_head(repo)
    assert _git_status_clean(repo)

    with monkeypatch.context() as mp:
        mp.setattr(
            mod,
            "revision_download_archive",
            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sentinel-revision-download")),
        )
        r = client.get("/api/entities/p_revdl/revisions/1/download")
        assert r.status_code == 400

    assert _git_head(repo) == head_before
    assert _git_status_clean(repo)


def test_inventory_json_routes_delegate_to_core_read_models(web_mod, monkeypatch: pytest.MonkeyPatch):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()
    client = app.test_client()

    head_before = _git_head(repo)
    assert _git_status_clean(repo)

    expected_items = [
        {
            "sfid": "p_from_core",
            "name": "From Core",
            "description": "",
            "category": "",
            "uom": "ea",
            "total": 7,
            "by_location": {"l_main": 7},
            "as_of": "2026-01-01T00:00:00Z",
        }
    ]
    expected_item = dict(expected_items[0])

    with monkeypatch.context() as mp:
        mp.setattr(mod, "inventory_list_items_readonly", lambda *a, **k: expected_items)
        mp.setattr(mod, "inventory_view_item_readonly", lambda *a, **k: expected_item)

        listing = client.get("/api/inventory")
        assert listing.status_code == 200
        listing_body = listing.get_json() or {}
        assert listing_body.get("success") is True
        assert listing_body.get("items") == expected_items

        view = client.get("/api/inventory/p_from_core")
        assert view.status_code == 200
        view_body = view.get_json() or {}
        assert view_body.get("success") is True
        assert view_body.get("item") == expected_item

    assert _git_head(repo) == head_before
    assert _git_status_clean(repo)


def test_bom_deep_route_delegates_to_core_read_model(web_mod, monkeypatch: pytest.MonkeyPatch):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()
    client = app.test_client()

    create_entity(repo, "p_root", {"name": "Root"})

    expected_nodes = [
        {
            "parent": "p_root",
            "use": "p_child",
            "name": "Child",
            "qty": 1,
            "rev": "released",
            "resolved_rev": "released",
            "level": 1,
            "is_alt": False,
            "alternates_group": None,
            "gross_qty": 1,
            "cycle": False,
            "onhand_total": 0,
        }
    ]

    with monkeypatch.context() as mp:
        mp.setattr(mod, "ent_resolved_bom_view", lambda *a, **k: expected_nodes)
        resp = client.get("/api/entities/p_root/bom/deep")
        assert resp.status_code == 200
        body = resp.get_json() or {}
        assert body.get("success") is True
        assert body.get("nodes") == expected_nodes
