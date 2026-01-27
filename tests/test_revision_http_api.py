from __future__ import annotations
import subprocess
from pathlib import Path
import importlib.util

import pytest

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity, cut_revision


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


def test_revisions_bump_endpoint_creates_and_releases(web_mod):
    mod = web_mod
    app = mod.app

    repo = mod.get_datarepo_path()
    assert isinstance(repo, Path)

    # Ensure entity exists
    create_entity(repo, "p_http1", {"name": "HTTP Rev"})

    client = app.test_client()

    # Initial state: no revs
    r0 = client.get("/api/entities/p_http1/revisions")
    assert r0.status_code == 200
    d0 = r0.get_json()
    assert d0.get("success") is True
    assert d0.get("rev") in (None, "")
    assert isinstance(d0.get("revisions"), list) and len(d0.get("revisions")) == 0

    # Bump endpoint in web does: bump -> release new rev
    r1 = client.post("/api/entities/p_http1/revisions/bump", json={"notes": "via http"})
    assert r1.status_code == 200
    d1 = r1.get_json()
    assert d1.get("success") is True
    assert d1.get("rev") == "1"

    # The returned revisions list should include the released snapshot '1'
    revs = d1.get("revisions") or []
    m1 = next((m for m in revs if (m.get("id") or m.get("rev")) == "1"), None)
    assert m1 is not None
    assert m1.get("status") == "released"
    assert (m1.get("released_at") or "").strip() != ""

    # And released pointer should be updated on disk
    released_fp = repo / "entities" / "p_http1" / "refs" / "released"
    assert released_fp.exists()
    assert released_fp.read_text().strip() == "1"


def test_revisions_release_specific_rev_endpoint(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_http2", {"name": "HTTP Rev2"})

    # Prepare a draft snapshot '1' using core API
    cut_revision(repo, "p_http2", rev="1", notes="draft via core")

    client = app.test_client()

    # Release that specific revision via HTTP
    r = client.post("/api/entities/p_http2/revisions/1/release", json={"notes": "release via http"})
    assert r.status_code == 200
    data = r.get_json()
    assert data.get("success") is True
    assert data.get("rev") == "1"

    # meta.yml should reflect released status
    meta_fp = repo / "entities" / "p_http2" / "revisions" / "1" / "meta.yml"
    import yaml
    meta = yaml.safe_load(meta_fp.read_text()) or {}
    assert meta.get("status") == "released"
    assert (meta.get("released_at") or "").strip() != ""

    # pointer updated
    released_fp = repo / "entities" / "p_http2" / "refs" / "released"
    assert released_fp.exists()
    assert released_fp.read_text().strip() == "1"


def test_revisions_get_lists_released_and_draft(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_http3", {"name": "HTTP Rev3"})
    client = app.test_client()

    # Create released rev 1 via bump endpoint
    r1 = client.post("/api/entities/p_http3/revisions/bump", json={"notes": "first"})
    assert r1.status_code == 200
    assert r1.get_json().get("rev") == "1"

    # Prepare a draft rev 2 using core API (no release)
    cut_revision(repo, "p_http3", rev="2", notes="draft second")

    # GET should show current released pointer and both revisions
    g = client.get("/api/entities/p_http3/revisions")
    assert g.status_code == 200
    body = g.get_json()
    assert body.get("success") is True
    assert body.get("rev") == "1"

    revs = body.get("revisions") or []
    ids = {m.get("id") or m.get("rev") for m in revs}
    assert {"1", "2"}.issubset(ids)
    # Find statuses
    m1 = next((m for m in revs if (m.get("id") or m.get("rev")) == "1"), {})
    m2 = next((m for m in revs if (m.get("id") or m.get("rev")) == "2"), {})
    assert m1.get("status") == "released"
    assert m2.get("status") == "draft"
