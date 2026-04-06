from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import FileSystemLoader

from conftest import import_web_app_module, init_git_repo
from smallfactory.core.v1.entities import bom_add_line, create_entity
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
