"""Tests for web API entity CRUD routes — list, get, update, retire,
and files upload/download/delete via HTTP endpoints.

Note: Entity creation has no JSON API endpoint; it uses a form-based
HTML route (/entities/add). We test via the core API for setup, then
test the JSON endpoints that do exist."""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    app = mod.app
    app.config["TESTING"] = True
    with app.test_client() as c:
        c._repo = repo
        c._mod = mod
        yield c


def _repo(client) -> Path:
    return client._repo


# ---------------------------------------------------------------------------
# Entity listing and retrieval (GET /api/entities, GET /api/entities/<sfid>)
# ---------------------------------------------------------------------------

class TestEntityListAndGet:

    def test_list_entities_empty(self, client):
        resp = client.get("/api/entities")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert isinstance(data["entities"], list)
        assert len(data["entities"]) == 0

    def test_list_entities_returns_created(self, client):
        create_entity(_repo(client), "p_a", {"name": "A"})
        create_entity(_repo(client), "p_b", {"name": "B"})
        resp = client.get("/api/entities")
        data = resp.get_json()
        sfids = [e["sfid"] for e in data["entities"]]
        assert "p_a" in sfids
        assert "p_b" in sfids

    def test_get_entity(self, client):
        create_entity(_repo(client), "p_widget", {"name": "Widget"})
        resp = client.get("/api/entities/p_widget")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["entity"]["sfid"] == "p_widget"
        assert data["entity"]["name"] == "Widget"

    def test_get_nonexistent_entity(self, client):
        resp = client.get("/api/entities/p_ghost")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Entity search (GET /api/entities/search?q=...)
# ---------------------------------------------------------------------------

class TestEntitySearch:

    def test_search_by_name(self, client):
        create_entity(_repo(client), "p_bolt", {"name": "M3 Bolt"})
        create_entity(_repo(client), "p_nut", {"name": "M3 Nut"})
        resp = client.get("/api/entities/search?q=bolt")
        assert resp.status_code == 200
        data = resp.get_json()
        sfids = [e["sfid"] for e in data["results"]]
        assert "p_bolt" in sfids
        assert "p_nut" not in sfids

    def test_search_by_sfid(self, client):
        create_entity(_repo(client), "p_cap100", {"name": "Capacitor 100uF"})
        resp = client.get("/api/entities/search?q=cap100")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["sfid"] == "p_cap100"

    def test_search_empty_query(self, client):
        create_entity(_repo(client), "p_test", {"name": "Test"})
        resp = client.get("/api/entities/search?q=")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Entity update (POST /api/entities/<sfid>/update)
# ---------------------------------------------------------------------------

class TestEntityUpdate:

    def test_update_fields(self, client):
        create_entity(_repo(client), "p_upd", {"name": "Original"})
        resp = client.post(
            "/api/entities/p_upd/update",
            json={"updates": {"name": "Updated", "category": "IC"}},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["entity"]["name"] == "Updated"
        assert data["entity"]["category"] == "IC"

    def test_update_top_level_shorthand(self, client):
        """The route accepts either {updates: {...}} or bare field dict."""
        create_entity(_repo(client), "p_upd2", {"name": "Orig"})
        resp = client.post(
            "/api/entities/p_upd2/update",
            json={"name": "Changed"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["entity"]["name"] == "Changed"

    def test_update_nonexistent_entity(self, client):
        resp = client.post(
            "/api/entities/p_ghost/update",
            json={"name": "Ghost"},
        )
        assert resp.status_code == 400

    def test_update_preserves_existing_fields(self, client):
        create_entity(_repo(client), "p_upd3", {"name": "Orig", "category": "IC"})
        client.post(
            "/api/entities/p_upd3/update",
            json={"manufacturer": "TI"},
        )
        resp = client.get("/api/entities/p_upd3")
        ent = resp.get_json()["entity"]
        assert ent["name"] == "Orig"
        assert ent["category"] == "IC"
        assert ent["manufacturer"] == "TI"


# ---------------------------------------------------------------------------
# Entity retirement (POST /entities/<sfid>/retire — form-based, redirects)
# ---------------------------------------------------------------------------

class TestEntityRetire:

    def test_retire_entity_redirects(self, client):
        create_entity(_repo(client), "p_old", {"name": "Old"})
        resp = client.post(
            "/entities/p_old/retire",
            follow_redirects=False,
        )
        # Form-based route redirects back to the entity view
        assert resp.status_code in (302, 303)

    def test_retired_entity_has_flag(self, client):
        create_entity(_repo(client), "p_old2", {"name": "Old2"})
        # Don't follow redirects — the redirect target renders HTML templates
        client.post("/entities/p_old2/retire", follow_redirects=False)
        # Verify via JSON API
        resp = client.get("/api/entities/p_old2")
        ent = resp.get_json()["entity"]
        assert ent.get("retired") is True


# ---------------------------------------------------------------------------
# Entity specs (GET /api/entities/specs/<sfid>)
# ---------------------------------------------------------------------------

class TestEntitySpecs:

    def test_specs_for_part(self, client):
        resp = client.get("/api/entities/specs/p_test")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Files upload / list / download / delete via web API
# ---------------------------------------------------------------------------

class TestEntityFilesApi:

    def test_upload_and_list_file(self, client):
        create_entity(_repo(client), "p_files", {"name": "Files Test"})
        data = {
            "file": (io.BytesIO(b"hello world"), "readme.txt"),
            "path": "readme.txt",
        }
        resp = client.post(
            "/api/entities/p_files/files/upload",
            data=data,
            content_type="multipart/form-data",
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

        # List files
        resp = client.get("/api/entities/p_files/files")
        assert resp.status_code == 200
        items = resp.get_json().get("items", [])
        names = [i["name"] for i in items]
        assert "readme.txt" in names

    def test_download_file(self, client):
        create_entity(_repo(client), "p_dl", {"name": "DL Test"})
        client.post(
            "/api/entities/p_dl/files/upload",
            data={"file": (io.BytesIO(b"content"), "doc.txt"), "path": "doc.txt"},
            content_type="multipart/form-data",
        )
        resp = client.get("/api/entities/p_dl/files/download?path=doc.txt")
        assert resp.status_code == 200
        assert resp.data == b"content"

    def test_delete_file(self, client):
        create_entity(_repo(client), "p_rm", {"name": "RM Test"})
        client.post(
            "/api/entities/p_rm/files/upload",
            data={"file": (io.BytesIO(b"data"), "temp.txt"), "path": "temp.txt"},
            content_type="multipart/form-data",
        )
        resp = client.post(
            "/api/entities/p_rm/files/delete",
            json={"path": "temp.txt"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["success"] is True

    def test_download_nonexistent_file(self, client):
        create_entity(_repo(client), "p_nofile", {"name": "No File"})
        resp = client.get("/api/entities/p_nofile/files/download?path=ghost.txt")
        assert resp.status_code == 404

    def test_mkdir_and_rmdir(self, client):
        create_entity(_repo(client), "p_dirs", {"name": "Dirs Test"})
        # Create directory
        resp = client.post(
            "/api/entities/p_dirs/files/mkdir",
            json={"path": "docs"},
        )
        assert resp.status_code == 200

        # List should show the directory
        resp = client.get("/api/entities/p_dirs/files")
        items = resp.get_json()["items"]
        dir_names = [i["name"] for i in items if i["type"] == "dir"]
        assert "docs" in dir_names

        # Remove directory
        resp = client.post(
            "/api/entities/p_dirs/files/rmdir",
            json={"path": "docs"},
        )
        assert resp.status_code == 200
