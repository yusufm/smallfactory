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


def _git_commit_count(root: Path) -> int:
    r = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    try:
        return int((r.stdout or "0").strip() or "0")
    except Exception:
        return 0


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_one_post_mutation_creates_exactly_one_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    before = _git_commit_count(repo)

    client = mod.app.test_client()
    resp = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_child", "qty": 1, "rev": "released"},
    )
    assert resp.status_code == 200
    assert resp.get_json().get("success") is True

    after = _git_commit_count(repo)
    assert after == before + 1
