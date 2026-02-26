"""Shared test helpers and fixtures for smallFactory tests."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

# Ensure project root on sys.path so 'smallfactory' package is importable when running pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def init_git_repo(root: Path) -> None:
    """Initialise a bare git repo with a test identity at *root*."""
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def import_web_app_module() -> object:
    """Dynamically import ``web/app.py`` without starting the server."""
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


def git_commit_count(root: Path) -> int:
    """Return the number of commits reachable from HEAD (0 if none)."""
    r = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return int((r.stdout or "0").strip() or "0")
