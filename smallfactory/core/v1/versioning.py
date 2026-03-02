from __future__ import annotations

import re
from pathlib import Path
from typing import Tuple

from .config import SF_TOOL_VERSION, load_datarepo_config


def _parse_semver_like(raw: str) -> Tuple[int, int, int]:
    s = str(raw or "").strip()
    if not s:
        return (0, 0, 0)
    # Ignore semver pre-release/build metadata for compatibility comparison.
    core = s.split("-", 1)[0].split("+", 1)[0]
    parts = core.split(".")
    nums: list[int] = []
    for p in parts[:3]:
        m = re.search(r"\d+", p or "")
        nums.append(int(m.group(0)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def get_repo_declared_version(repo_path: Path) -> str:
    try:
        cfg = load_datarepo_config(repo_path)
    except Exception:
        cfg = {}
    # Backward compat: read legacy compat_version if present.
    raw = str((cfg or {}).get("smallfactory_version") or (cfg or {}).get("compat_version") or SF_TOOL_VERSION).strip()
    return raw or SF_TOOL_VERSION


def get_version_state(repo_path: Path) -> str:
    rv = _parse_semver_like(get_repo_declared_version(repo_path))
    tv = _parse_semver_like(SF_TOOL_VERSION)
    if rv < tv:
        return "repo_older"
    if rv > tv:
        return "repo_newer"
    return "match"


def assert_repo_version_matches_tool(repo_path: Path) -> None:
    repo_version = get_repo_declared_version(repo_path)
    state = get_version_state(repo_path)
    if state == "match":
        return
    if state == "repo_older":
        raise RuntimeError(
            f"Repo version {repo_version} is older than tool version {SF_TOOL_VERSION}. "
            "Run 'sf repo upgrade' before continuing."
        )
    raise RuntimeError(
        f"Repo version {repo_version} is newer than tool version {SF_TOOL_VERSION}. "
        "Upgrade the tool before continuing."
    )
