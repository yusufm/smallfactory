from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _git_last_commit_author(root: Path) -> tuple[str, str]:
    name = subprocess.run(["git", "log", "-n", "1", "--pretty=%an"], cwd=root, capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "log", "-n", "1", "--pretty=%ae"], cwd=root, capture_output=True, text=True).stdout.strip()
    return name, email


def test_bom_add_sets_commit_author_from_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    headers = {
        "X-Forwarded-User": "Jane Doe",
        "X-Forwarded-Email": "jane.doe@example.com",
    }

    client = mod.app.test_client()
    resp = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_child", "qty": 1, "rev": "released"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True

    an, ae = _git_last_commit_author(repo)
    assert an == "Jane Doe"
    assert ae == "jane.doe@example.com"
