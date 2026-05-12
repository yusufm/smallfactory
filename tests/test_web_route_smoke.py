from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import FileSystemLoader

from conftest import import_web_app_module, init_git_repo
from smallfactory.core.v1.entities import bom_add_line, bom_alt_add, create_entity
from smallfactory.core.v1.inventory import inventory_post
from smallfactory.core.v1.repo import write_datarepo_config

pytest.importorskip("flask", reason="Flask not installed; web route tests skipped")


@pytest.fixture()
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    write_datarepo_config(repo)
    create_entity(repo, "p_widget", {"name": "Widget"})
    create_entity(repo, "p_child", {"name": "Child"})
    create_entity(repo, "b_test_001", {"name": "Build Record", "part_sfid": "p_widget"})
    bom_add_line(repo, "p_widget", use="p_child", qty=1, rev="released")

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")
    mod.app.config["TESTING"] = True
    template_root = Path(__file__).resolve().parents[1] / "web" / "templates"
    mod.app.template_folder = str(template_root)
    mod.app.jinja_loader = FileSystemLoader(str(template_root))

    with mod.app.test_client() as client:
        client._repo = repo
        yield client


@pytest.mark.parametrize(
    ("path", "follow_redirects"),
    [
        ("/", False),
        ("/inventory", False),
        ("/inventory/adjust", False),
        ("/entities", False),
        ("/entities/add", False),
        ("/entities/p_widget", False),
        ("/entities/p_widget/build", False),
        ("/entities/p_widget/bom/import", False),
        ("/entities/p_widget/bom-tree", False),
        ("/entities/b_test_001", False),
        ("/stickers", True),
        ("/stickers/batch", False),
        ("/vision", False),
        ("/announcements", False),
        ("/repo/stats", False),
    ],
)
def test_canonical_web_pages_render_successfully(client, path: str, follow_redirects: bool):
    resp = client.get(path, follow_redirects=follow_redirects)

    assert resp.status_code == 200
    assert resp.content_type.startswith("text/html")


def test_build_entity_view_renders_status_field(client):
    create_entity(
        client._repo,
        "b_test_002",
        {
            "name": "Build In Progress",
            "part_sfid": "p_widget",
            "status": "in_progress",
            "opened_at": "2026-04-05T12:00:00Z",
        },
    )

    resp = client.get("/entities/b_test_002")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Build In Progress" in body
    assert "in_progress" in body


def test_dashboard_recent_builds_uses_build_statuses(client):
    create_entity(
        client._repo,
        "b_test_003",
        {
            "name": "Build Complete",
            "part_sfid": "p_widget",
            "status": "completed",
            "opened_at": "2026-04-05T12:00:00Z",
        },
    )

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Build Complete" in body
    assert "completed" in body


def test_builds_list_shows_build_status_not_entity_state(client):
    create_entity(
        client._repo,
        "b_test_004",
        {
            "name": "Build Open",
            "part_sfid": "p_widget",
            "status": "open",
        },
    )

    resp = client.get("/entities?type=b")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Build Open" in body
    assert "open" in body
    assert "Active" not in body


def test_build_readiness_api_returns_shortage_summary(client):
    resp = client.get("/api/entities/p_widget/build-readiness?build_qty=2")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    readiness = data["readiness"]
    assert readiness["build_qty"] == 2
    assert readiness["shortage_count"] >= 0
    assert readiness["missing_revision_count"] == 0


def test_build_readiness_api_rejects_missing_and_non_part_entities(client):
    missing = client.get("/api/entities/p_missing/build-readiness")
    non_part = client.get("/api/entities/b_test_001/build-readiness")

    assert missing.status_code == 404
    assert missing.get_json()["success"] is False
    assert non_part.status_code == 400
    assert non_part.get_json()["success"] is False


def test_build_readiness_counts_stocked_alternate(client):
    create_entity(client._repo, "p_alt_child", {"name": "Alternate Child"})
    create_entity(client._repo, "l_main", {"name": "Main Stock"})
    bom_alt_add(client._repo, "p_widget", index=0, alt_use="p_alt_child")
    inventory_post(client._repo, "p_alt_child", 1, l_sfid="l_main")

    resp = client.get("/api/entities/p_widget/build-readiness")

    assert resp.status_code == 200
    readiness = resp.get_json()["readiness"]
    assert readiness["can_build_qty"] >= 1
    assert readiness["shortage_count"] == 0
    assert readiness["rows"][0]["covered_by_alternate"] is True


def test_revision_manifest_endpoint_returns_artifact_summary(client):
    # Create a released snapshot so the manifest endpoint has revision metadata.
    mod = import_web_app_module()
    mod.bump_revision(client._repo, "p_child", rev="1")
    mod.release_revision(client._repo, "p_child", "1")

    resp = client.get("/api/entities/p_child/revisions/1/manifest")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["manifest"]["rev"] == "1"
    assert data["manifest"]["artifact_count"] >= 1


def test_revision_manifest_endpoint_scans_legacy_snapshot_files(client):
    rev_dir = client._repo / "entities" / "p_child" / "revisions" / "legacy"
    rev_dir.mkdir(parents=True)
    (rev_dir / "meta.yml").write_text("rev: legacy\nstatus: released\n", encoding="utf-8")
    (rev_dir / "entity.yml").write_text("name: Legacy Child\n", encoding="utf-8")

    resp = client.get("/api/entities/p_child/revisions/legacy/manifest")

    assert resp.status_code == 200
    manifest = resp.get_json()["manifest"]
    assert manifest["artifact_count"] >= 1
    assert any(a["path"] == "entity.yml" for a in manifest["artifacts"])


def test_entity_revision_table_includes_manifest_ui(client):
    resp = client.get("/entities/p_child")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Source" in body
    assert "Files" in body
    assert "View manifest" in body
    assert "/api/entities/${encodeURIComponent(SFID)}/revisions/${encodeURIComponent(id)}/manifest" in body
