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
    # Enable autocommit by default
    monkeypatch.setenv("SF_WEB_AUTOCOMMIT", "1")

    return mod


def test_bom_add_nonexistent_child_returns_400(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    # Parent exists, child does not
    create_entity(repo, "p_parent", {"name": "Parent"})

    client = app.test_client()
    r = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_missing", "qty": 1, "rev": "released"},
    )
    assert r.status_code == 400
    jd = r.get_json()
    assert jd.get("success") is False
    assert "does not exist" in (jd.get("error") or "")
    # BOM remains empty
    assert bom_list(repo, "p_parent") == []


def test_bom_set_invalid_index_and_unsupported_field(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})

    client = app.test_client()
    # Seed one line
    ok = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_c1", "qty": 2, "rev": "released"},
    )
    assert ok.status_code == 200 and ok.get_json().get("success") is True

    # Invalid index
    r_bad_idx = client.post(
        "/api/entities/p_parent/bom/set",
        json={"index": 5, "qty": 3},
    )
    assert r_bad_idx.status_code == 400
    assert r_bad_idx.get_json().get("success") is False

    # Unsupported field
    r_bad_field = client.post(
        "/api/entities/p_parent/bom/set",
        json={"index": 0, "foo": "bar"},
    )
    # HTTP layer filters unsupported fields, so this should still succeed and make no change
    assert r_bad_field.status_code == 200
    jd = r_bad_field.get_json()
    assert jd.get("success") is True
    # Verify no change to line contents
    bom = bom_list(repo, "p_parent")
    assert bom[0].get("use") == "p_c1"
    assert int(bom[0].get("qty", 0)) == 2

    # Invalid 'use' (empty)
    r_bad_use = client.post(
        "/api/entities/p_parent/bom/set",
        json={"index": 0, "use": ""},
    )
    assert r_bad_use.status_code == 400
    assert r_bad_use.get_json().get("success") is False


def test_bom_remove_invalid_params_and_not_found(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})
    create_entity(repo, "p_c2", {"name": "Child2"})

    client = app.test_client()

    # Seed two lines
    assert client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_c1", "qty": 1, "rev": "released"},
    ).get_json().get("success") is True
    assert client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_c2", "qty": 1, "rev": "released"},
    ).get_json().get("success") is True

    # Neither index nor use -> 400
    r_none = client.post("/api/entities/p_parent/bom/remove", json={})
    assert r_none.status_code == 400

    # Both index and use -> 400
    r_both = client.post(
        "/api/entities/p_parent/bom/remove",
        json={"index": 0, "use": "p_c1"},
    )
    assert r_both.status_code == 400

    # Index out of range -> 400
    r_oob = client.post(
        "/api/entities/p_parent/bom/remove",
        json={"index": 9},
    )
    assert r_oob.status_code == 400

    # Remove by use not present -> 400
    r_use_nf = client.post(
        "/api/entities/p_parent/bom/remove",
        json={"use": "p_missing", "remove_all": "1"},
    )
    assert r_use_nf.status_code == 400


def test_bom_alt_add_and_remove_invalids(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})

    client = app.test_client()

    # Seed one line
    assert client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_c1", "qty": 1, "rev": "released"},
    ).get_json().get("success") is True

    # alt-add: index out of range -> 400
    r_alt_bad_idx = client.post(
        "/api/entities/p_parent/bom/alt-add",
        json={"index": 5, "alt_use": "p_c1"},
    )
    assert r_alt_bad_idx.status_code == 400

    # alt-add: non-existent alt_use -> 400
    r_alt_missing = client.post(
        "/api/entities/p_parent/bom/alt-add",
        json={"index": 0, "alt_use": "p_nope"},
    )
    assert r_alt_missing.status_code == 400

    # alt-remove: missing both alt_index and alt_use -> 400
    r_alt_remove_none = client.post(
        "/api/entities/p_parent/bom/alt-remove",
        json={"index": 0},
    )
    assert r_alt_remove_none.status_code == 400

    # alt-remove: alt_index out of range -> first add one valid alt to create list
    create_entity(repo, "p_alt", {"name": "Alt"})
    assert client.post(
        "/api/entities/p_parent/bom/alt-add",
        json={"index": 0, "alt_use": "p_alt"},
    ).get_json().get("success") is True

    r_alt_remove_oob = client.post(
        "/api/entities/p_parent/bom/alt-remove",
        json={"index": 0, "alt_index": 9},
    )
    assert r_alt_remove_oob.status_code == 400

    # alt-remove: alt_use not present
    r_alt_remove_nf = client.post(
        "/api/entities/p_parent/bom/alt-remove",
        json={"index": 0, "alt_use": "p_missing"},
    )
    assert r_alt_remove_nf.status_code == 400
