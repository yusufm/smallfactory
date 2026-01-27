from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

pytest.importorskip("flask", reason="Flask not installed; web metrics tests skipped")


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


def test_compute_git_metrics_upstream_missing_reports_unknown(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    mod = _import_web_app_module()
    m = mod._compute_git_metrics(repo)

    assert m.get("is_repo") is True
    st = (m.get("status") or {})
    assert st.get("ahead") is None
    assert st.get("behind") is None
    assert st.get("upstream_ok") is False
    err = st.get("upstream_error")
    assert err
    low = str(err).lower()
    assert "upstream" in low or "fatal" in low
