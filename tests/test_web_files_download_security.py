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
