from __future__ import annotations

from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module

pytest.importorskip("flask", reason="Flask not installed; web metrics tests skipped")


def test_compute_git_metrics_upstream_missing_reports_unknown(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    mod = import_web_app_module()
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
