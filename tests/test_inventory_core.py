from __future__ import annotations

import json
import threading
import os
from pathlib import Path

import pytest
import yaml

from conftest import init_git_repo, git_commit_count
from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1 import inventory as inventory_mod
from smallfactory.core.v1.inventory import (
    inventory_onhand,
    inventory_onhand_readonly,
    inventory_post,
    inventory_rebuild,
)


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
    init_git_repo(p)
    create_entity(p, "p_inv", {"name": "Inventory Part", "uom": "pcs"})
    create_entity(p, "l_main", {"name": "Main"})
    create_entity(p, "l_overflow", {"name": "Overflow"})
    return p


def test_inventory_post_writes_journal_and_caches(repo: Path):
    (repo / "sfdatarepo.yml").write_text("inventory:\n  default_location: l_main\n", encoding="utf-8")

    posted = inventory_post(repo, "p_inv", 5, l_sfid=None, reason="initial load")
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
    inventory_post(repo, "p_inv", 2, l_sfid="l_main")
    inventory_post(repo, "p_inv", 5, l_sfid="l_overflow")

    with pytest.raises(ValueError, match="on-hand at l_main"):
        inventory_post(repo, "p_inv", -3, l_sfid="l_main")

    with pytest.raises(ValueError, match="total on-hand"):
        inventory_post(repo, "p_inv", -8, l_sfid="l_overflow")

    journal = repo / "inventory" / "p_inv" / "journal.ndjson"
    lines = [ln for ln in journal.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 2


def test_inventory_post_serializes_concurrent_negative_checks(repo: Path, monkeypatch: pytest.MonkeyPatch):
    inventory_post(repo, "p_inv", 5, l_sfid="l_main")

    barrier = threading.Barrier(2)
    original_compute = inventory_mod._compute_part_onhand_from_journal

    def _compute_with_overlap(journal_path: Path):
        try:
            barrier.wait(timeout=0.2)
        except threading.BrokenBarrierError:
            pass
        return original_compute(journal_path)

    monkeypatch.setattr(inventory_mod, "_compute_part_onhand_from_journal", _compute_with_overlap)

    results: list[dict] = []
    errors: list[str] = []

    def _worker():
        try:
            results.append(inventory_post(repo, "p_inv", -4, l_sfid="l_main"))
        except Exception as e:
            errors.append(str(e))

    t1 = threading.Thread(target=_worker)
    t2 = threading.Thread(target=_worker)
    t1.start()
    t2.start()
    t1.join(timeout=3)
    t2.join(timeout=3)

    assert len(results) == 1
    assert len(errors) == 1
    assert "below zero" in errors[0]
    onhand = inventory_onhand(repo, part="p_inv")
    assert int(onhand.get("total", -1)) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock behavior test")
def test_exclusive_journal_lock_times_out_when_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock_target = tmp_path / "journal.ndjson"

    import fcntl

    def _always_block(*args, **kwargs):
        raise BlockingIOError("busy")

    ticks = iter([0.0, 0.02, 0.05, 0.12])

    def _fake_monotonic():
        try:
            return next(ticks)
        except StopIteration:
            return 999.0

    monkeypatch.setattr(fcntl, "flock", _always_block)
    monkeypatch.setattr(inventory_mod.time, "monotonic", _fake_monotonic)
    monkeypatch.setattr(inventory_mod.time, "sleep", lambda *_: None)

    with pytest.raises(TimeoutError, match="Timed out acquiring inventory journal lock"):
        with inventory_mod._exclusive_journal_lock(lock_target, timeout_seconds=0.1, poll_interval_seconds=0.01):
            pass


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

    before = git_commit_count(repo)
    rebuilt = inventory_rebuild(repo)
    after = git_commit_count(repo)

    assert rebuilt["parts"] == ["p_inv", "p_other"]
    assert rebuilt["locations"] == ["l_main", "l_overflow"]
    assert after == before + 1

    part_cache = yaml.safe_load((repo / "inventory" / "p_inv" / "onhand.generated.yml").read_text()) or {}
    assert part_cache["total"] == 5
    assert part_cache["by_location"] == {"l_main": 4, "l_overflow": 1}

    loc_cache = yaml.safe_load((repo / "inventory" / "_location" / "l_main" / "onhand.generated.yml").read_text()) or {}
    assert loc_cache["parts"] == {"p_inv": 4, "p_other": 2}
    assert loc_cache["total"] == 6
