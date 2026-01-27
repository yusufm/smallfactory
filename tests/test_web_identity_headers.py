from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_last_commit_author(root: Path) -> tuple[str, str]:
    name = subprocess.run(["git", "log", "-n", "1", "--pretty=%an"], cwd=root, capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "log", "-n", "1", "--pretty=%ae"], cwd=root, capture_output=True, text=True).stdout.strip()
    return name, email


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_bom_add_sets_commit_author_from_headers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
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
