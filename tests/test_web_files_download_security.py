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


def test_files_download_rejects_traversal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    create_entity(repo, "p_sec", {"name": "Sec"})

    client = mod.app.test_client()
    r = client.get("/api/entities/p_sec/files/download?path=../evil.txt")
    assert r.status_code == 400
    data = r.get_json() or {}
    assert data.get("success") is False
