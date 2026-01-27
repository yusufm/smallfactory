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


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def test_autopush_sync_push_failure_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    monkeypatch.setenv("SF_WEB_AUTOPUSH", "1")
    monkeypatch.setenv("SF_WEB_AUTOPUSH_ASYNC", "0")
    monkeypatch.setenv("SF_GIT_PUSH_TTL_SEC", "0")

    def _raise(*_a, **_k):
        raise RuntimeError("sentinel-push")

    monkeypatch.setattr(mod, "git_push", _raise)

    client = mod.app.test_client()
    resp = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_child", "qty": 1, "rev": "released"},
    )
    assert resp.status_code == 400
    data = resp.get_json() or {}
    assert data.get("success") is False
    assert "git push" in (data.get("error") or "").lower()


def test_autopush_async_push_failure_does_not_fail_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})

    monkeypatch.setenv("SF_WEB_AUTOPUSH", "1")
    monkeypatch.setenv("SF_WEB_AUTOPUSH_ASYNC", "1")
    monkeypatch.setenv("SF_GIT_PUSH_TTL_SEC", "0")

    def _raise(*_a, **_k):
        raise RuntimeError("sentinel-push")

    monkeypatch.setattr(mod, "git_push", _raise)

    def _spawn_sync(path: Path):
        mod._push_worker(path)

    monkeypatch.setattr(mod, "_spawn_async_push", _spawn_sync)

    client = mod.app.test_client()
    resp = client.post(
        "/api/entities/p_parent/bom/add",
        json={"use": "p_child", "qty": 1, "rev": "released"},
    )
    assert resp.status_code == 200
    data = resp.get_json() or {}
    assert data.get("success") is True
