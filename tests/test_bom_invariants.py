from __future__ import annotations

import subprocess
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import bom_add_line, bom_set_line, create_entity


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def test_bom_add_line_rejects_duplicate_use(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})

    bom_add_line(repo, "p_parent", use="p_c1", qty=1, rev="released")

    with pytest.raises(ValueError):
        bom_add_line(repo, "p_parent", use="p_c1", qty=2, rev="released")


def test_bom_set_line_rejects_duplicate_use(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_c1", {"name": "Child1"})
    create_entity(repo, "p_c2", {"name": "Child2"})

    bom_add_line(repo, "p_parent", use="p_c1", qty=1, rev="released")
    bom_add_line(repo, "p_parent", use="p_c2", qty=1, rev="released")

    with pytest.raises(ValueError):
        bom_set_line(repo, "p_parent", index=1, updates={"use": "p_c1"})
