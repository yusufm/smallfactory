from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from conftest import git_commit_count

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"


def _entrypoint_env(data_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["HOME"] = str(data_path / "home")
    env["SF_DATA_PATH"] = str(data_path)
    env["SF_REPO_PATH"] = str(data_path / "datarepo")
    env["PORT"] = "8080"
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    return env


def test_entrypoint_bootstrap_is_safe_under_parallel_first_run(tmp_path: Path):
    data_path = tmp_path / "data"
    data_path.mkdir(parents=True)
    env = _entrypoint_env(data_path)

    p1 = subprocess.Popen(
        ["bash", str(ENTRYPOINT), "repo", "status"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    p2 = subprocess.Popen(
        ["bash", str(ENTRYPOINT), "repo", "status"],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    out1, err1 = p1.communicate(timeout=30.0)
    out2, err2 = p2.communicate(timeout=30.0)

    assert p1.returncode == 0, err1 or out1
    assert p2.returncode == 0, err2 or out2

    repo = data_path / "datarepo"
    assert (repo / ".git").is_dir()
    assert (repo / "sfdatarepo.yml").is_file()
    assert (repo / "entities" / "l_inbox" / "entity.yml").is_file()
    assert git_commit_count(repo) == 2

    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert (status.stdout or "").strip() == ""
