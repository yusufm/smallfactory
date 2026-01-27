from __future__ import annotations
import os
import sys
import subprocess
from pathlib import Path
import tempfile
import importlib.util
import types

import pytest

# Ensure project root on sys.path so 'smallfactory' package is importable when running pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.gitutils import git_commit_paths


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Configure minimal identity for commits
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_has_commit(root: Path) -> bool:
    r = subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return r.returncode == 0


def _git_last_commit_message(root: Path) -> str:
    r = subprocess.run(["git", "log", "-n", "1", "--pretty=%B"], cwd=root, capture_output=True, text=True)
    return (r.stdout or "").strip()


def test_git_commit_paths_commit_only(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    # Create a file and commit via git_commit_paths
    entities_dir = repo / "entities" / "p_widget"
    entities_dir.mkdir(parents=True)
    f = entities_dir / "entity.yml"
    f.write_text("name: Widget\n")

    msg = "[test] commit-only for entities/p_widget"
    git_commit_paths(repo, [entities_dir], msg)

    assert _git_has_commit(repo), "Expected a commit to exist"
    last = _git_last_commit_message(repo)
    assert "commit-only" in last


def test_git_commit_paths_noop_when_nothing_to_commit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    entities_dir = repo / "entities" / "p_widget"
    entities_dir.mkdir(parents=True)
    f = entities_dir / "entity.yml"
    f.write_text("name: Widget\n")

    msg = "[test] first commit"
    git_commit_paths(repo, [entities_dir], msg)
    assert _git_has_commit(repo)
    first = _git_last_commit_message(repo)

    # Re-commit without changes should be a no-op and should not raise
    git_commit_paths(repo, [entities_dir], "[test] second commit")
    second = _git_last_commit_message(repo)
    assert second == first


@pytest.mark.skipif(pytest.importorskip("flask", reason="Flask not installed; web txn tests skipped") is None, reason="Flask not installed")
def test_run_repo_txn_autocommit_on_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Dynamically import web/app.py to access _run_repo_txn without starting the server
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Make project root importable similar to app.py behavior
    import sys
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore

    assert hasattr(mod, "_run_repo_txn")

    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    # Common mutate: create file under entities path
    target_dir = repo / "entities" / "p_test"
    target_dir.mkdir(parents=True)
    target_file = target_dir / "entity.yml"

    def mutate():
        target_file.write_text("name: Test\n")
        return {"ok": True}

    # Ensure no remote to avoid pull/push side-effects
    # By default, repo has no remotes; autopush should be a no-op

    # Web txn wrapper should not create commits on its own.
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")
    res = mod._run_repo_txn(repo, mutate)
    assert res["ok"]
    assert not _git_has_commit(repo), "Web txn wrapper should not create commits"

    # Still should not create commits.
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    def mutate2():
        # change file again to have diff
        target_file.write_text("name: Test 2\n")
        return {"ok": True}

    res2 = mod._run_repo_txn(repo, mutate2)
    assert res2["ok"]
    assert not _git_has_commit(repo), "Web layer should not create commits; core APIs must commit explicitly"
