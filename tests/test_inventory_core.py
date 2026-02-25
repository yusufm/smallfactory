from __future__ import annotations

import json
import subprocess
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.inventory import (
    inventory_onhand,
    inventory_onhand_readonly,
    inventory_list_items_readonly,
    inventory_view_item_readonly,
    inventory_post,
    inventory_rebuild,
)
from smallfactory.core.v1.validate import validate_repo


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_commit_count(root: Path) -> int:
    r = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0:
        return 0
    return int((r.stdout or "0").strip() or "0")


def _write_journal(repo: Path, part: str, rows: list[dict]) -> Path:
    p = repo / "inventory" / part / "journal.ndjson"
    p.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    p.write_text(text, encoding="utf-8")
    return p


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    _init_git_repo(p)
    create_entity(p, "p_inv", {"name": "Inventory Part", "uom": "pcs"})
    create_entity(p, "l_main", {"name": "Main"})
    create_entity(p, "l_overflow", {"name": "Overflow"})
    return p


def test_inventory_post_writes_journal_and_caches(repo: Path):
    (repo / "sfdatarepo.yml").write_text("inventory:\n  default_location: l_main\n", encoding="utf-8")

    posted = inventory_post(repo, "p_inv", 5, location=None, reason="initial load")
    assert posted["part"] == "p_inv"
    assert posted["location"] == "l_main"
    assert posted["qty_delta"] == 5
    assert len(posted["txn"]) == 26

    journal = repo / "inventory" / "p_inv" / "journal.ndjson"
    lines = [ln for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    line = json.loads(lines[0])
    assert line["location"] == "l_main"
    assert line["qty_delta"] == 5
    assert line["reason"] == "initial load"

    part_cache = inventory_onhand(repo, part="p_inv")
    assert part_cache["uom"] == "pcs"
    assert part_cache["total"] == 5
    assert part_cache["by_location"] == {"l_main": 5}

    loc_cache = inventory_onhand(repo, location="l_main")
    assert loc_cache["parts"]["p_inv"] == 5
    assert loc_cache["total"] == 5


def test_inventory_post_blocks_location_negative_even_if_global_total_positive(repo: Path):
    inventory_post(repo, "p_inv", 2, location="l_main")
    inventory_post(repo, "p_inv", 5, location="l_overflow")

    with pytest.raises(ValueError, match="on-hand at l_main"):
        inventory_post(repo, "p_inv", -3, location="l_main")

    with pytest.raises(ValueError, match="total on-hand"):
        inventory_post(repo, "p_inv", -8, location="l_overflow")

    journal = repo / "inventory" / "p_inv" / "journal.ndjson"
    lines = [ln for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2


def test_readonly_onhand_computes_without_materializing_caches(repo: Path):
    _write_journal(
        repo,
        "p_inv",
        [
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "location": "l_main", "qty_delta": 3},
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAW", "location": "l_overflow", "qty_delta": 2},
        ],
    )

    part_cache_file = repo / "inventory" / "p_inv" / "onhand.generated.yml"
    loc_cache_file = repo / "inventory" / "_location" / "l_main" / "onhand.generated.yml"
    assert not part_cache_file.exists()
    assert not loc_cache_file.exists()

    ro_part = inventory_onhand_readonly(repo, part="p_inv")
    assert ro_part["by_location"] == {"l_main": 3, "l_overflow": 2}
    assert ro_part["total"] == 5
    assert not part_cache_file.exists()

    ro_loc = inventory_onhand_readonly(repo, location="l_main")
    assert ro_loc["parts"] == {"p_inv": 3}
    assert ro_loc["total"] == 3
    assert not loc_cache_file.exists()

    materialized = inventory_onhand(repo, part="p_inv")
    assert materialized["total"] == 5
    assert part_cache_file.exists()


def test_inventory_list_and_view_readonly_helpers(repo: Path):
    create_entity(repo, "p_other", {"name": "Other Part", "category": "raw", "uom": "kg"})
    inventory_post(repo, "p_inv", 3, location="l_main")

    rows = inventory_list_items_readonly(repo)
    assert [r.get("sfid") for r in rows] == ["p_inv", "p_other"]

    inv_row = next(r for r in rows if r.get("sfid") == "p_inv")
    assert inv_row["name"] == "Inventory Part"
    assert inv_row["total"] == 3
    assert inv_row["by_location"] == {"l_main": 3}

    other_row = next(r for r in rows if r.get("sfid") == "p_other")
    assert other_row["uom"] == "kg"
    assert other_row["total"] == 0
    assert other_row["by_location"] == {}

    item = inventory_view_item_readonly(repo, "p_inv")
    assert item["sfid"] == "p_inv"
    assert item["name"] == "Inventory Part"
    assert item["total"] == 3
    assert item["by_location"] == {"l_main": 3}


def test_inventory_rebuild_recreates_all_caches_from_journals(repo: Path):
    create_entity(repo, "p_other", {"name": "Other Part"})
    _write_journal(
        repo,
        "p_inv",
        [
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAX", "location": "l_main", "qty_delta": 4},
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAY", "location": "l_overflow", "qty_delta": 1},
        ],
    )
    _write_journal(
        repo,
        "p_other",
        [
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAZ", "location": "l_main", "qty_delta": 2},
        ],
    )

    before = _git_commit_count(repo)
    rebuilt = inventory_rebuild(repo)
    after = _git_commit_count(repo)

    assert rebuilt["parts"] == ["p_inv", "p_other"]
    assert rebuilt["locations"] == ["l_main", "l_overflow"]
    assert after == before + 1

    part_cache = yaml.safe_load((repo / "inventory" / "p_inv" / "onhand.generated.yml").read_text()) or {}
    assert part_cache["total"] == 5
    assert part_cache["by_location"] == {"l_main": 4, "l_overflow": 1}

    loc_cache = yaml.safe_load((repo / "inventory" / "_location" / "l_main" / "onhand.generated.yml").read_text()) or {}
    assert loc_cache["parts"] == {"p_inv": 4, "p_other": 2}
    assert loc_cache["total"] == 6

    # Rebuild commits must include required sfid tokens.
    lint = validate_repo(repo, include_entities=False, include_inventory=False, include_git=True)
    codes = {i.get("code") for i in lint.get("issues", [])}
    assert "GIT_TOKEN_REQUIRED" not in codes


def test_inventory_post_rejects_non_part_sfid(repo: Path):
    create_entity(repo, "l_not_a_part", {"name": "Not A Part"})
    with pytest.raises(ValueError, match="part must be a valid part sfid"):
        inventory_post(repo, "l_not_a_part", 1, location="l_main")


def test_inventory_rebuild_errors_on_historical_negative_onhand(repo: Path):
    _write_journal(
        repo,
        "p_inv",
        [
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "location": "l_main", "qty_delta": 1},
            {"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAW", "location": "l_main", "qty_delta": -2},
        ],
    )
    with pytest.raises(ValueError, match="negative .*on-hand"):
        inventory_rebuild(repo)
