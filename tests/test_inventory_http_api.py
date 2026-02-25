from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_commit_count(root: Path) -> int:
    r = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return int((r.stdout or "0").strip() or "0")


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


@pytest.fixture()
def web_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")
    return mod


def test_api_inventory_adjust_uses_default_location_and_absolute_quantity(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_inv", {"name": "Part"})
    create_entity(repo, "l_main", {"name": "Main"})
    (repo / "sfdatarepo.yml").write_text("inventory:\n  default_location: l_main\n", encoding="utf-8")

    client = app.test_client()

    r1 = client.post("/api/inventory/adjust", json={"sfid": "p_inv", "quantity": 4})
    assert r1.status_code == 200
    d1 = r1.get_json() or {}
    assert d1.get("success") is True
    assert d1.get("l_sfid") == "l_main"
    assert d1.get("delta") == 4
    assert d1.get("new_qty") == 4
    assert d1.get("total") == 4

    r2 = client.post("/api/inventory/adjust", json={"sfid": "p_inv", "quantity": 2})
    assert r2.status_code == 200
    d2 = r2.get_json() or {}
    assert d2.get("success") is True
    assert d2.get("l_sfid") == "l_main"
    assert d2.get("delta") == -2
    assert d2.get("new_qty") == 2
    assert d2.get("total") == 2

    onhand = client.get("/api/inventory/onhand?sfid=p_inv")
    assert onhand.status_code == 200
    h = onhand.get_json() or {}
    assert h.get("success") is True
    assert h.get("l_sfid") == "l_main"
    assert h.get("location_qty") == 2
    assert h.get("total") == 2


def test_api_inventory_adjust_delta_zero_is_noop_and_onhand_requires_sfid(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_zero", {"name": "Zero Part"})
    create_entity(repo, "l_main", {"name": "Main"})

    client = app.test_client()

    missing = client.get("/api/inventory/onhand")
    assert missing.status_code == 400
    missing_body = missing.get_json() or {}
    assert missing_body.get("success") is False
    assert "Missing required parameter: sfid" in (missing_body.get("error") or "")

    before = _git_commit_count(repo)
    noop = client.post("/api/inventory/adjust", json={"sfid": "p_zero", "l_sfid": "l_main", "delta": 0})
    after = _git_commit_count(repo)
    assert noop.status_code == 200
    body = noop.get_json() or {}
    assert body.get("success") is True
    assert body.get("delta") == 0
    assert body.get("new_qty") == 0
    assert body.get("total") == 0
    assert after == before
