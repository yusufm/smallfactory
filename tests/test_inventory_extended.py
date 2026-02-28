"""Extended tests for smallfactory.core.v1.inventory — covers default location
resolution, negative-guard edge cases, readonly vs mutating onhand, rebuild
semantics, and mixed-location scenarios."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from conftest import init_git_repo, git_commit_count
from smallfactory.core.v1.entities import create_entity
from smallfactory.core.v1.inventory import (
    inventory_onhand,
    inventory_onhand_readonly,
    inventory_post,
    inventory_rebuild,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    init_git_repo(p)
    create_entity(p, "p_bolt", {"name": "M3 Bolt", "uom": "pcs"})
    create_entity(p, "p_nut", {"name": "M3 Nut", "uom": "pcs"})
    create_entity(p, "l_main", {"name": "Main"})
    create_entity(p, "l_spare", {"name": "Spare"})
    return p


def _set_default_location(repo: Path, loc: str):
    cfg_path = repo / "sfdatarepo.yml"
    cfg_path.write_text(f"inventory:\n  default_location: {loc}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Default location resolution
# ---------------------------------------------------------------------------

class TestDefaultLocationResolution:

    def test_uses_default_when_location_omitted(self, repo: Path):
        _set_default_location(repo, "l_main")
        result = inventory_post(repo, "p_bolt", 5, l_sfid=None)
        assert result["location"] == "l_main"

    def test_explicit_location_overrides_default(self, repo: Path):
        _set_default_location(repo, "l_main")
        result = inventory_post(repo, "p_bolt", 3, l_sfid="l_spare")
        assert result["location"] == "l_spare"

    def test_no_default_and_no_location_raises(self, repo: Path):
        # No sfdatarepo.yml => no default
        with pytest.raises(ValueError, match="location is required"):
            inventory_post(repo, "p_bolt", 5, l_sfid=None)

    def test_invalid_location_prefix_raises(self, repo: Path):
        create_entity(repo, "p_not_a_loc", {"name": "Not Location"})
        with pytest.raises(ValueError, match="location must be.*l_"):
            inventory_post(repo, "p_bolt", 1, l_sfid="p_not_a_loc")


# ---------------------------------------------------------------------------
# Negative on-hand guard
# ---------------------------------------------------------------------------

class TestNegativeGuard:

    def test_total_below_zero_rejected(self, repo: Path):
        _set_default_location(repo, "l_main")
        inventory_post(repo, "p_bolt", 3)
        with pytest.raises(ValueError, match="total on-hand.*below zero"):
            inventory_post(repo, "p_bolt", -4)

    def test_per_location_below_zero_rejected(self, repo: Path):
        inventory_post(repo, "p_bolt", 5, l_sfid="l_main")
        inventory_post(repo, "p_bolt", 3, l_sfid="l_spare")
        # Total is 8, but l_spare only has 3 — removing 4 from l_spare should fail
        with pytest.raises(ValueError, match="below zero"):
            inventory_post(repo, "p_bolt", -4, l_sfid="l_spare")

    def test_exact_zero_allowed(self, repo: Path):
        _set_default_location(repo, "l_main")
        inventory_post(repo, "p_bolt", 5)
        result = inventory_post(repo, "p_bolt", -5)
        assert result["onhand"]["total"] == 0

    def test_zero_delta_rejected(self, repo: Path):
        _set_default_location(repo, "l_main")
        with pytest.raises(ValueError, match="non-zero"):
            inventory_post(repo, "p_bolt", 0)


# ---------------------------------------------------------------------------
# Entity validation
# ---------------------------------------------------------------------------

class TestInventoryEntityValidation:

    def test_nonexistent_part_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError, match="Part sfid"):
            inventory_post(repo, "p_ghost", 1, l_sfid="l_main")

    def test_nonexistent_location_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError, match="Location sfid"):
            inventory_post(repo, "p_bolt", 1, l_sfid="l_nonexistent")


# ---------------------------------------------------------------------------
# inventory_onhand_readonly vs inventory_onhand
# ---------------------------------------------------------------------------

class TestOnhandReadonly:

    def test_readonly_does_not_create_cache_files(self, repo: Path):
        _set_default_location(repo, "l_main")
        inventory_post(repo, "p_bolt", 10)
        # Delete caches
        for f in (repo / "inventory").rglob("onhand.generated.yml"):
            f.unlink()
        before_commit = git_commit_count(repo)
        result = inventory_onhand_readonly(repo, part="p_bolt")
        after_commit = git_commit_count(repo)
        assert result["total"] == 10
        # No new commits should have been made
        assert after_commit == before_commit
        # Cache file should NOT be recreated
        assert not (repo / "inventory" / "p_bolt" / "onhand.generated.yml").exists()

    def test_readonly_by_location(self, repo: Path):
        inventory_post(repo, "p_bolt", 5, l_sfid="l_main")
        inventory_post(repo, "p_bolt", 3, l_sfid="l_spare")
        result = inventory_onhand_readonly(repo, location="l_main")
        assert result["parts"]["p_bolt"] == 5
        assert result["total"] == 5

    def test_readonly_summary(self, repo: Path):
        _set_default_location(repo, "l_main")
        inventory_post(repo, "p_bolt", 7)
        inventory_post(repo, "p_nut", 12)
        result = inventory_onhand_readonly(repo)
        sfids = [p["sfid"] for p in result["parts"]]
        assert "p_bolt" in sfids
        assert "p_nut" in sfids
        assert result["total"] == 19


# ---------------------------------------------------------------------------
# Multi-location scenarios
# ---------------------------------------------------------------------------

class TestMultiLocation:

    def test_post_to_multiple_locations(self, repo: Path):
        inventory_post(repo, "p_bolt", 10, l_sfid="l_main")
        inventory_post(repo, "p_bolt", 5, l_sfid="l_spare")
        onhand = inventory_onhand(repo, part="p_bolt")
        assert onhand["total"] == 15
        assert onhand["by_location"]["l_main"] == 10
        assert onhand["by_location"]["l_spare"] == 5

    def test_transfer_between_locations(self, repo: Path):
        inventory_post(repo, "p_bolt", 10, l_sfid="l_main")
        # "Transfer": remove from main, add to spare
        inventory_post(repo, "p_bolt", -3, l_sfid="l_main")
        inventory_post(repo, "p_bolt", 3, l_sfid="l_spare")
        onhand = inventory_onhand(repo, part="p_bolt")
        assert onhand["by_location"]["l_main"] == 7
        assert onhand["by_location"]["l_spare"] == 3
        assert onhand["total"] == 10


# ---------------------------------------------------------------------------
# inventory_rebuild
# ---------------------------------------------------------------------------

class TestInventoryRebuild:

    def test_rebuild_regenerates_caches(self, repo: Path):
        inventory_post(repo, "p_bolt", 5, l_sfid="l_main")
        inventory_post(repo, "p_nut", 3, l_sfid="l_spare")

        result = inventory_rebuild(repo)

        # Rebuild must report all parts and locations it processed
        assert "p_bolt" in result["parts"]
        assert "p_nut" in result["parts"]
        assert "l_main" in result["locations"]
        assert "l_spare" in result["locations"]

        # Verify caches exist and contain correct totals
        import yaml
        bolt_cache = yaml.safe_load(
            (repo / "inventory" / "p_bolt" / "onhand.generated.yml").read_text()
        )
        assert bolt_cache["total"] == 5
        assert bolt_cache["by_location"]["l_main"] == 5

        nut_cache = yaml.safe_load(
            (repo / "inventory" / "p_nut" / "onhand.generated.yml").read_text()
        )
        assert nut_cache["total"] == 3
        assert nut_cache["by_location"]["l_spare"] == 3

    def test_rebuild_empty_inventory(self, repo: Path):
        result = inventory_rebuild(repo)
        assert result["parts"] == []
        assert result["locations"] == []


# ---------------------------------------------------------------------------
# Commit metadata (::sfid:: tokens in commit messages)
# ---------------------------------------------------------------------------

class TestCommitMetadata:

    def test_inventory_post_commit_includes_sfid_tokens(self, repo: Path):
        import subprocess
        inventory_post(repo, "p_bolt", 5, l_sfid="l_main")
        log = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"], cwd=repo,
            capture_output=True, text=True,
        ).stdout
        assert "::sfid::p_bolt" in log
        assert "::sfid::l_main" in log
