"""Extended tests for smallfactory.core.v1.entities — covers list_entities,
update_entity_field/fields, retire_entity, delete_entity prohibition,
bom_remove_line, bom_set_line, bom_alt_add, and bom_alt_remove."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from conftest import init_git_repo
from smallfactory.core.v1.entities import (
    bom_add_line,
    bom_alt_add,
    bom_alt_remove,
    bom_list,
    bom_remove_line,
    bom_set_line,
    create_entity,
    delete_entity,
    get_entity,
    list_entities,
    retire_entity,
    update_entity_field,
    update_entity_fields,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    init_git_repo(p)
    return p


# ---------------------------------------------------------------------------
# list_entities
# ---------------------------------------------------------------------------

class TestListEntities:

    def test_empty_repo(self, repo: Path):
        assert list_entities(repo) == []

    def test_returns_all_entities(self, repo: Path):
        create_entity(repo, "p_alpha", {"name": "Alpha"})
        create_entity(repo, "p_beta", {"name": "Beta"})
        create_entity(repo, "l_shelf", {"name": "Shelf"})
        ents = list_entities(repo)
        sfids = [e["sfid"] for e in ents]
        assert "p_alpha" in sfids
        assert "p_beta" in sfids
        assert "l_shelf" in sfids

    def test_sorted_alphabetically(self, repo: Path):
        create_entity(repo, "p_zulu", {"name": "Z"})
        create_entity(repo, "p_alpha", {"name": "A"})
        ents = list_entities(repo)
        sfids = [e["sfid"] for e in ents]
        assert sfids == sorted(sfids)

    def test_skips_dirs_without_entity_yml(self, repo: Path):
        create_entity(repo, "p_real", {"name": "Real"})
        # Create a rogue directory with no entity.yml
        (repo / "entities" / "p_ghost").mkdir(parents=True)
        ents = list_entities(repo)
        sfids = [e["sfid"] for e in ents]
        assert "p_real" in sfids
        assert "p_ghost" not in sfids


# ---------------------------------------------------------------------------
# update_entity_field / update_entity_fields
# ---------------------------------------------------------------------------

class TestUpdateEntityField:

    def test_update_single_field(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        result = update_entity_field(repo, "p_item", "category", "resistor")
        assert result["category"] == "resistor"
        assert result["sfid"] == "p_item"
        # Verify persistence
        ent = get_entity(repo, "p_item")
        assert ent["category"] == "resistor"

    def test_cannot_update_sfid(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        with pytest.raises(ValueError, match="immutable"):
            update_entity_field(repo, "p_item", "sfid", "p_other")

    def test_empty_field_name_raises(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        with pytest.raises(ValueError):
            update_entity_field(repo, "p_item", "", "value")

    def test_nonexistent_entity_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError):
            update_entity_field(repo, "p_nosuch", "name", "Ghost")


class TestUpdateEntityFields:

    def test_update_multiple_fields(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        result = update_entity_fields(repo, "p_item", {
            "category": "capacitor",
            "manufacturer": "ACME",
        })
        assert result["category"] == "capacitor"
        assert result["manufacturer"] == "ACME"

    def test_cannot_include_sfid(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        with pytest.raises(ValueError, match="sfid"):
            update_entity_fields(repo, "p_item", {"sfid": "p_other", "name": "Changed"})

    def test_empty_updates_raises(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item"})
        with pytest.raises(ValueError, match="non-empty"):
            update_entity_fields(repo, "p_item", {})

    def test_preserves_existing_fields(self, repo: Path):
        create_entity(repo, "p_item", {"name": "Item", "category": "IC"})
        update_entity_fields(repo, "p_item", {"manufacturer": "TI"})
        ent = get_entity(repo, "p_item")
        assert ent["name"] == "Item"
        assert ent["category"] == "IC"
        assert ent["manufacturer"] == "TI"


# ---------------------------------------------------------------------------
# retire_entity / delete_entity
# ---------------------------------------------------------------------------

class TestRetireEntity:

    def test_retire_marks_entity(self, repo: Path):
        create_entity(repo, "p_old", {"name": "Old Part"})
        result = retire_entity(repo, "p_old", reason="Obsolete")
        assert result["retired"] is True
        assert "retired_at" in result
        assert result["retired_reason"] == "Obsolete"

    def test_retired_entity_still_retrievable(self, repo: Path):
        create_entity(repo, "p_old", {"name": "Old Part"})
        retire_entity(repo, "p_old")
        ent = get_entity(repo, "p_old")
        assert ent["retired"] is True

    def test_retire_without_reason(self, repo: Path):
        create_entity(repo, "p_old", {"name": "Old Part"})
        result = retire_entity(repo, "p_old")
        assert result["retired"] is True
        assert "retired_reason" not in result


class TestDeleteEntity:

    def test_hard_delete_is_prohibited(self, repo: Path):
        create_entity(repo, "p_del", {"name": "Delete Me"})
        with pytest.raises(RuntimeError, match="Hard delete.*disabled"):
            delete_entity(repo, "p_del")

    def test_hard_delete_with_force_still_prohibited(self, repo: Path):
        create_entity(repo, "p_del", {"name": "Delete Me"})
        with pytest.raises(RuntimeError, match="Hard delete.*disabled"):
            delete_entity(repo, "p_del", force=True)


# ---------------------------------------------------------------------------
# bom_remove_line
# ---------------------------------------------------------------------------

class TestBomRemoveLine:

    def _setup_bom(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child1", {"name": "C1"})
        create_entity(repo, "p_child2", {"name": "C2"})
        create_entity(repo, "p_child3", {"name": "C3"})
        bom_add_line(repo, "p_parent", use="p_child1", qty=1)
        bom_add_line(repo, "p_parent", use="p_child2", qty=2)
        bom_add_line(repo, "p_parent", use="p_child3", qty=3)

    def test_remove_by_index(self, repo: Path):
        self._setup_bom(repo)
        result = bom_remove_line(repo, "p_parent", index=1)
        assert 1 in result["removed"]
        uses = [l["use"] for l in result["bom"]]
        assert "p_child2" not in uses
        assert "p_child1" in uses
        assert "p_child3" in uses

    def test_remove_by_use(self, repo: Path):
        self._setup_bom(repo)
        result = bom_remove_line(repo, "p_parent", use="p_child1")
        uses = [l["use"] for l in result["bom"]]
        assert "p_child1" not in uses

    def test_remove_index_out_of_range(self, repo: Path):
        self._setup_bom(repo)
        with pytest.raises(IndexError, match="out of range"):
            bom_remove_line(repo, "p_parent", index=99)

    def test_remove_nonexistent_use_raises(self, repo: Path):
        self._setup_bom(repo)
        with pytest.raises(ValueError, match="No BOM line found"):
            bom_remove_line(repo, "p_parent", use="p_doesnt_exist")

    def test_must_provide_exactly_one_of_index_or_use(self, repo: Path):
        self._setup_bom(repo)
        with pytest.raises(ValueError, match="exactly one"):
            bom_remove_line(repo, "p_parent")  # neither
        with pytest.raises(ValueError, match="exactly one"):
            bom_remove_line(repo, "p_parent", index=0, use="p_child1")  # both

    def test_remove_last_line_clears_bom_key(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "P"})
        create_entity(repo, "p_child", {"name": "C"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        bom_remove_line(repo, "p_parent", index=0)
        ent = get_entity(repo, "p_parent")
        # When all lines removed, bom key should be absent
        assert "bom" not in ent or ent.get("bom") in (None, [])

    def test_bom_only_on_parts(self, repo: Path):
        create_entity(repo, "l_loc", {"name": "Location"})
        with pytest.raises(ValueError, match="part entities"):
            bom_remove_line(repo, "l_loc", index=0)


# ---------------------------------------------------------------------------
# bom_set_line
# ---------------------------------------------------------------------------

class TestBomSetLine:

    def test_update_qty(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child", {"name": "Child"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        result = bom_set_line(repo, "p_parent", index=0, updates={"qty": 10})
        assert result["line"]["qty"] == 10
        assert result["bom"][0]["qty"] == 10

    def test_update_rev(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child", {"name": "Child"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        result = bom_set_line(repo, "p_parent", index=0, updates={"rev": "2"})
        assert result["line"]["rev"] == "2"

    def test_rejects_unsupported_fields(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child", {"name": "Child"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        with pytest.raises(ValueError, match="Unsupported BOM field"):
            bom_set_line(repo, "p_parent", index=0, updates={"color": "red"})

    def test_index_out_of_range(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child", {"name": "Child"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        with pytest.raises(IndexError):
            bom_set_line(repo, "p_parent", index=5, updates={"qty": 2})

    def test_change_use_to_duplicate_raises(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_a", {"name": "A"})
        create_entity(repo, "p_b", {"name": "B"})
        bom_add_line(repo, "p_parent", use="p_a", qty=1)
        bom_add_line(repo, "p_parent", use="p_b", qty=1)
        with pytest.raises(ValueError, match="Duplicate"):
            bom_set_line(repo, "p_parent", index=1, updates={"use": "p_a"})


# ---------------------------------------------------------------------------
# bom_alt_add / bom_alt_remove
# ---------------------------------------------------------------------------

class TestBomAltAdd:

    def test_add_alternate(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_primary", {"name": "Primary"})
        create_entity(repo, "p_alt1", {"name": "Alt1"})
        bom_add_line(repo, "p_parent", use="p_primary", qty=1)

        result = bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt1")
        alts = result["line"].get("alternates", [])
        assert len(alts) == 1
        assert alts[0]["use"] == "p_alt1"

    def test_add_multiple_alternates(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_primary", {"name": "Primary"})
        create_entity(repo, "p_alt1", {"name": "Alt1"})
        create_entity(repo, "p_alt2", {"name": "Alt2"})
        bom_add_line(repo, "p_parent", use="p_primary", qty=1)

        bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt1")
        result = bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt2")
        alts = result["line"]["alternates"]
        assert len(alts) == 2

    def test_index_out_of_range(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_alt", {"name": "Alt"})
        with pytest.raises(IndexError):
            bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt")


class TestBomAltRemove:

    def _setup(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_primary", {"name": "Primary"})
        create_entity(repo, "p_alt1", {"name": "Alt1"})
        create_entity(repo, "p_alt2", {"name": "Alt2"})
        bom_add_line(repo, "p_parent", use="p_primary", qty=1)
        bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt1")
        bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt2")

    def test_remove_by_alt_index(self, repo: Path):
        self._setup(repo)
        result = bom_alt_remove(repo, "p_parent", index=0, alt_index=0)
        assert result["removed"]["use"] == "p_alt1"
        alts = result["line"].get("alternates", [])
        assert len(alts) == 1

    def test_remove_by_alt_use(self, repo: Path):
        self._setup(repo)
        result = bom_alt_remove(repo, "p_parent", index=0, alt_use="p_alt2")
        assert result["removed"]["use"] == "p_alt2"

    def test_remove_last_alternate_removes_key(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_primary", {"name": "Primary"})
        create_entity(repo, "p_alt", {"name": "Alt"})
        bom_add_line(repo, "p_parent", use="p_primary", qty=1)
        bom_alt_add(repo, "p_parent", index=0, alt_use="p_alt")
        result = bom_alt_remove(repo, "p_parent", index=0, alt_index=0)
        assert "alternates" not in result["line"]

    def test_must_provide_exactly_one_identifier(self, repo: Path):
        self._setup(repo)
        with pytest.raises(ValueError, match="exactly one"):
            bom_alt_remove(repo, "p_parent", index=0)  # neither
        with pytest.raises(ValueError, match="exactly one"):
            bom_alt_remove(repo, "p_parent", index=0, alt_index=0, alt_use="p_alt1")

    def test_no_alternates_raises(self, repo: Path):
        create_entity(repo, "p_parent", {"name": "Parent"})
        create_entity(repo, "p_child", {"name": "Child"})
        bom_add_line(repo, "p_parent", use="p_child", qty=1)
        with pytest.raises(ValueError, match="No alternates"):
            bom_alt_remove(repo, "p_parent", index=0, alt_index=0)

    def test_nonexistent_alt_use_raises(self, repo: Path):
        self._setup(repo)
        with pytest.raises(ValueError, match="No alternate"):
            bom_alt_remove(repo, "p_parent", index=0, alt_use="p_doesnt_exist")
