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


# ---------------------------------------------------------------------------
# Build Journal API (GET/append events, tags editing)
# ---------------------------------------------------------------------------

class TestBuildJournalApi:

    def test_append_and_get_events(self, client):
        create_entity(_repo(client), "b_unit_001", {"name": "Build Unit 001"})

        r1 = client.post(
            "/api/entities/b_unit_001/events/append",
            json={
                "event": {
                    "tags": ["repair_request"],
                    "message": "No USB enumeration",
                }
            },
        )
        assert r1.status_code == 200
        d1 = r1.get_json()
        assert d1["success"] is True
        assert d1["event"]["id"]

        r3 = client.get("/api/entities/b_unit_001/events")
        assert r3.status_code == 200
        d3 = r3.get_json()
        assert d3["success"] is True
        events = d3["events"]
        assert isinstance(events, list)
        assert len(events) == 1
        assert events[0]["tags"] == ["repair_request"]

    def test_append_rejects_unknown_event_fields(self, client):
        create_entity(_repo(client), "b_unit_006", {"name": "Build Unit 006"})
        r1 = client.post(
            "/api/entities/b_unit_006/events/append",
            json={"event": {"message": "x", "target": "p_uut"}},
        )
        assert r1.status_code == 400

    def test_append_rejects_invalid_event_id(self, client):
        create_entity(_repo(client), "b_unit_007", {"name": "Build Unit 007"})
        r1 = client.post(
            "/api/entities/b_unit_007/events/append",
            json={"event": {"id": "bad-id", "message": "x"}},
        )
        assert r1.status_code == 400

    def test_append_event_rejects_non_build_sfid(self, client):
        create_entity(_repo(client), "p_widget", {"name": "Widget"})
        resp = client.post(
            "/api/entities/p_widget/events/append",
            json={"event": {"tags": ["log"], "message": "hello"}},
        )
        assert resp.status_code == 400

    def test_get_events_rejects_non_build_sfid(self, client):
        create_entity(_repo(client), "p_widget2", {"name": "Widget 2"})
        resp = client.get("/api/entities/p_widget2/events")
        assert resp.status_code == 400

    def test_tags_optional_and_editable(self, client):
        create_entity(_repo(client), "b_unit_002", {"name": "Build Unit 002"})

        r1 = client.post(
            "/api/entities/b_unit_002/events/append",
            json={"event": {"message": "Operator note"}},
        )
        assert r1.status_code == 200
        d1 = r1.get_json()
        assert d1["success"] is True
        event_id = d1["event"]["id"]
        assert d1["event"]["tags"] == []

        r2 = client.post(
            f"/api/entities/b_unit_002/events/{event_id}/tags",
            json={"tags": ["qa_review", "retest"]},
        )
        assert r2.status_code == 200
        d2 = r2.get_json()
        assert d2["success"] is True
        assert d2["event"]["tags"] == ["qa_review", "retest"]
        assert d2["event"]["message"] == "Operator note"

    def test_update_event_entry(self, client):
        create_entity(_repo(client), "b_unit_004", {"name": "Build Unit 004"})
        r1 = client.post(
            "/api/entities/b_unit_004/events/append",
            json={"event": {"tags": ["note"], "message": "before"}},
        )
        assert r1.status_code == 200
        event_id = r1.get_json()["event"]["id"]

        r2 = client.post(
            f"/api/entities/b_unit_004/events/{event_id}/update",
            json={
                    "event": {
                    "tags": ["qa_review"],
                    "message": "after",
                    "files": ["event_attachments/test/evidence.txt"],
                }
            },
        )
        assert r2.status_code == 200
        d2 = r2.get_json()
        assert d2["success"] is True
        assert d2["event"]["id"] == event_id
        assert d2["event"]["tags"] == ["qa_review"]
        assert d2["event"]["message"] == "after"
        assert d2["event"]["files"] == ["event_attachments/test/evidence.txt"]

    def test_attach_file_link_to_event(self, client):
        create_entity(_repo(client), "b_unit_003", {"name": "Build Unit 003"})

        r1 = client.post(
            "/api/entities/b_unit_003/events/append",
            json={"event": {"message": "Attach test file"}},
        )
        assert r1.status_code == 200
        ev_id = r1.get_json()["event"]["id"]

        up = client.post(
            "/api/entities/b_unit_003/files/upload",
            data={"file": (io.BytesIO(b"abc"), "evidence.txt"), "path": f"event_attachments/{ev_id}/evidence.txt"},
            content_type="multipart/form-data",
        )
        assert up.status_code == 200
        up_data = up.get_json()
        assert up_data["success"] is True
        linked_path = up_data["result"]["path"]

        lk = client.post(
            f"/api/entities/b_unit_003/events/{ev_id}/files/link",
            json={"path": linked_path},
        )
        assert lk.status_code == 200
        lk_data = lk.get_json()
        assert lk_data["success"] is True
        assert linked_path in (lk_data["event"].get("files") or [])
