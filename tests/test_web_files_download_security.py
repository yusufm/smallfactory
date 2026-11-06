from __future__ import annotations

from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def test_files_download_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    create_entity(repo, "p_sec", {"name": "Sec"})

    client = mod.app.test_client()
    r = client.get("/api/entities/p_sec/files/download?path=../evil.txt")
    assert r.status_code == 400
    data = r.get_json() or {}
    assert data.get("success") is False


def test_files_download_sanitizes_content_disposition_filename(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setattr(
        mod,
        "files_stream_file",
        lambda *_a, **_kw: {
            "bytes": b"ok",
            "mimetype": "text/plain",
            "filename": 'bad"\r\nX-Injected: 1.txt',
        },
    )

    client = mod.app.test_client()
    r = client.get("/api/entities/p_sec/files/download?path=doc.txt")
    assert r.status_code == 200
    cd = r.headers.get("Content-Disposition", "")
    assert "\r" not in cd and "\n" not in cd
    assert 'filename="bad___X-Injected: 1.txt"' in cd


def test_view_template_escapes_bom_and_revision_dom_fields():
    view_html = (Path(__file__).resolve().parents[1] / "web" / "templates" / "entities" / "view.html").read_text(encoding="utf-8")
    assert "const nameEsc = _escapeHtml(String(r.name || r.use || ''));" in view_html
    assert "const altGrpEsc = _escapeHtml(String(r.alternates_group || ''));" in view_html
    assert "const ra = _escapeHtml(String(r.released_at||'—'));" in view_html
