from __future__ import annotations

from pathlib import Path

import pytest

from conftest import init_git_repo, git_commit_count, import_web_app_module
from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def test_one_post_mutation_creates_exactly_one_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    before = git_commit_count(repo)

    client = mod.app.test_client()
    resp = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_child", "qty": 1, "rev": "released"},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("success") is True

    after = git_commit_count(repo)
    assert after == before + 1
