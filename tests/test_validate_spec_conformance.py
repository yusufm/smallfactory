from __future__ import annotations
import os
import sys
import json
import subprocess
from pathlib import Path

import pytest

# Ensure project root on sys.path so 'smallfactory' package is importable when running pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.validate import validate_repo


# -----------------
# Helpers
# -----------------

def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _git_commit_all(root: Path, msg: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", msg], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _codes(issues):
    return {i.get("code") for i in (issues or [])}


# -----------------
# Entities layout and schema
# -----------------

def test_entities_root_missing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    res = validate_repo(repo, include_git=False)
    assert any(i["code"] == "ENT_ROOT_MISSING" for i in res["issues"])  # missing entities/


def test_entities_single_file_layout_error(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "foo.yml", "name: Foo\n")
    res = validate_repo(repo, include_git=False)
    assert "ENT_LAYOUT_SINGLE_FILE" in _codes(res["issues"])  # single-file layout not allowed


def test_invalid_sfid_dirname(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Invalid because of dash and uppercase
    (repo / "entities" / "P-bad").mkdir(parents=True)
    res = validate_repo(repo, include_git=False)
    assert "ENT_SFID_INVALID" in _codes(res["issues"])  # invalid sfid dir name


def test_entity_yml_missing(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "entities" / "p_part").mkdir(parents=True)
    res = validate_repo(repo, include_git=False)
    assert "ENT_ENTITY_YML_MISSING" in _codes(res["issues"])  # missing entity.yml


def test_entity_yml_forbidden_fields(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "p_ok" / "entity.yml", "sfid: p_ok\nchildren: []\nname: Ok\n")
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # both should be flagged
    assert "ENT_NO_SFID_FIELD" in codes
    assert "ENT_NO_CHILDREN" in codes


def test_bom_only_on_parts_and_bom_type(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Non-part with bom
    _write(repo / "entities" / "n_misc" / "entity.yml", "name: Misc\nbom: []\n")
    # Part with non-list bom
    _write(repo / "entities" / "p_parent" / "entity.yml", "name: P\nbom: {bad: true}\n")
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # check both rules
    assert "ENT_BOM_NON_PART" in codes
    assert "ENT_BOM_NOT_LIST" in codes


def test_bom_line_and_use_rules(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Part with several invalid bom lines
    _write(
        repo / "entities" / "p_parent" / "entity.yml",
        """
name: Parent
bom:
  - 1                      # not a map
  - {}                     # missing use
  - {use: INVALID}         # invalid sfid
  - {use: p_missing}       # missing entity
        """.lstrip(),
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # collect codes
    assert "ENT_BOM_LINE_NOT_MAP" in codes
    assert "ENT_BOM_USE_REQUIRED" in codes
    assert "ENT_BOM_USE_SFID_INVALID" in codes
    assert "ENT_BOM_USE_ENTITY_MISSING" in codes


def test_bom_alternates_validation(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(
        repo / "entities" / "p_parent" / "entity.yml",
        """
name: P
bom:
  - {use: p_child, alternates: 1}
  - {use: p_child2, alternates: [1]}
  - {use: p_child3, alternates: [{use: ''}]}
  - {use: p_child4, alternates: [{use: X}]}
  - {use: p_child5, alternates: [{use: l_missing}]}  # valid sfid but missing entity
        """.lstrip(),
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # ensure all alt validations surface
    assert "ENT_BOM_ALT_NOT_LIST" in codes
    assert "ENT_BOM_ALT_ITEM_NOT_MAP" in codes
    assert "ENT_BOM_ALT_USE_REQUIRED" in codes
    assert "ENT_BOM_ALT_SFID_INVALID" in codes
    assert "ENT_BOM_ALT_ENTITY_MISSING" in codes


def test_bom_duplicate_use_and_cycle_detection(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Duplicate children under p_dup
    _write(
        repo / "entities" / "p_child" / "entity.yml",
        "name: Child\n",
    )
    _write(
        repo / "entities" / "p_dup" / "entity.yml",
        """
name: Dup
bom:
  - {use: p_child}
  - {use: p_child}
        """.lstrip(),
    )
    # Cycle: p_a -> p_b -> p_a
    _write(repo / "entities" / "p_a" / "entity.yml", "name: A\nbom: [{use: p_b}]\n")
    _write(repo / "entities" / "p_b" / "entity.yml", "name: B\nbom: [{use: p_a}]\n")

    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # both issues should be present
    assert "ENT_BOM_USE_DUPLICATE" in codes
    assert "ENT_BOM_CYCLE" in codes


# -----------------
# Inventory rules
# -----------------

def test_inventory_root_missing_warns(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    res = validate_repo(repo, include_git=False)
    assert any(i["code"] == "INV_ROOT_MISSING" and i["severity"] == "warning" for i in res["issues"])  # inventory optional


def test_inventory_default_location_validation(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # inventory root must exist for inventory config validation to run
    (repo / "inventory").mkdir(parents=True, exist_ok=True)
    # invalid default_location (not l_*)
    _write(repo / "sfdatarepo.yml", "inventory:\n  default_location: x_foo\n")
    res = validate_repo(repo, include_git=False)
    assert "INV_DEFAULT_LOCATION_INVALID" in _codes(res["issues"])  # must be l_*

    # valid-looking but missing entity
    _write(repo / "sfdatarepo.yml", "inventory:\n  default_location: l_main\n")
    res2 = validate_repo(repo, include_git=False)
    assert "INV_DEFAULT_LOCATION_MISSING_ENTITY" in _codes(res2["issues"])  # entity missing


def test_inventory_part_dir_without_entity_and_missing_journal(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "inventory" / "p_foo").mkdir(parents=True)
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # only missing corresponding entity should be flagged
    assert "INV_PART_ENTITY_MISSING" in codes


def test_inventory_journal_validation(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Create corresponding entity for part and a valid location entity for one case
    _write(repo / "entities" / "p_foo" / "entity.yml", "name: Foo\n")
    _write(repo / "entities" / "l_main" / "entity.yml", "name: Main Location\n")

    j = repo / "inventory" / "p_foo" / "journal.ndjson"
    lines = []
    lines.append("not-json\n")  # INV_JOURNAL_JSON
    lines.append(json.dumps(123) + "\n")  # INV_JOURNAL_OBJ
    lines.append(json.dumps({"qty_delta": 1}) + "\n")  # missing txn => INV_JOURNAL_TXN_REQUIRED
    lines.append(json.dumps({"txn": "not-ulid", "qty_delta": 1}) + "\n")  # INV_JOURNAL_TXN_FORMAT
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAV", "foo": 1}) + "\n")  # missing qty_delta => INV_JOURNAL_QTY_REQUIRED
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAW", "qty_delta": 0, "ts": "x"}) + "\n")  # forbidden ts
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAX", "qty_delta": 0, "uom": "ea"}) + "\n")  # forbidden uom
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAY", "qty_delta": "x"}) + "\n")  # qty not int
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FAZ", "qty_delta": 1, "location": "x_bad"}) + "\n")  # INV_LOCATION_INVALID
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FB0", "qty_delta": 1, "location": "l_UPPER"}) + "\n")  # INV_LOCATION_SFID_INVALID
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FB1", "qty_delta": 1, "location": "l_missing"}) + "\n")  # INV_LOCATION_ENTITY_MISSING
    # Negative on-hand: +1 then -3 -> running total negative
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FB2", "qty_delta": 1}) + "\n")
    lines.append(json.dumps({"txn": "01ARZ3NDEKTSV4RRFFQ69G5FB3", "qty_delta": -3, "location": "l_main"}) + "\n")

    _write(j, "".join(lines))

    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    # Core schema/format checks
    assert "INV_JOURNAL_JSON" in codes
    assert "INV_JOURNAL_OBJ" in codes
    assert "INV_JOURNAL_TXN_REQUIRED" in codes
    assert "INV_JOURNAL_TXN_FORMAT" in codes
    assert "INV_JOURNAL_QTY_REQUIRED" in codes
    assert "INV_JOURNAL_FORBIDDEN_FIELD" in codes
    assert "INV_JOURNAL_QTY_NOT_INT" in codes
    assert "INV_LOCATION_INVALID" in codes
    assert "INV_LOCATION_SFID_INVALID" in codes
    assert "INV_LOCATION_ENTITY_MISSING" in codes
    assert "INV_NEGATIVE_ONHAND" in codes


# -----------------
# Git commit token rule
# -----------------

def test_git_commit_token_required(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    # Touch entities file and commit WITHOUT ::sfid:: token
    _write(repo / "entities" / "p_x" / "entity.yml", "name: X\n")
    _git_commit_all(repo, "[test] touch entities without token")

    res = validate_repo(repo, include_git=True)
    assert any(i["code"] == "GIT_TOKEN_REQUIRED" for i in res["issues"])  # must include ::sfid:: token when touching PLM


def test_entity_yml_invalid_yaml(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Invalid YAML (just a colon)
    _write(repo / "entities" / "p_bad" / "entity.yml", ":\n")
    res = validate_repo(repo, include_git=False)
    assert "ENT_ENTITY_YML_INVALID" in _codes(res["issues"])  # invalid yaml should be flagged


def test_bom_alternates_missing_use_is_ignored(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Create child so parent bom 'use' is valid and doesn't trigger missing entity
    _write(repo / "entities" / "p_child" / "entity.yml", "name: Child\n")
    # Alternates list contains objects without 'use' -> should be ignored (no alt-use errors)
    _write(
        repo / "entities" / "p_parent" / "entity.yml",
        """
name: Parent
bom:
  - {use: p_child, alternates: [{}, {foo: 1}]}
        """.lstrip(),
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # ensure no alt-specific errors are present
    assert "ENT_BOM_ALT_NOT_LIST" not in codes
    assert "ENT_BOM_ALT_ITEM_NOT_MAP" not in codes
    assert "ENT_BOM_ALT_USE_REQUIRED" not in codes
    assert "ENT_BOM_ALT_SFID_INVALID" not in codes
    assert "ENT_BOM_ALT_ENTITY_MISSING" not in codes


def test_gitattributes_union_merge_config_ok(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # inventory root must exist for inventory scan
    (repo / "inventory").mkdir(parents=True, exist_ok=True)
    # Provide .gitattributes with recommended union merge line
    _write(
        repo / ".gitattributes",
        "inventory/p_*/journal.ndjson merge=union\n",
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # no warnings about .gitattributes or union merge
    assert "INV_GITATTRIBUTES_MISSING" not in codes
    assert "INV_UNION_MERGE_MISSING" not in codes


def test_inventory_default_location_valid_ok(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # inventory root required for inventory checks
    (repo / "inventory").mkdir(parents=True, exist_ok=True)
    # Create location entity referenced by default_location
    _write(repo / "entities" / "l_main" / "entity.yml", "name: Main Location\n")
    # Configure repo default location to valid existing location
    _write(repo / "sfdatarepo.yml", "inventory:\n  default_location: l_main\n")
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])  # should not surface invalid/missing entity errors
    assert "INV_DEFAULT_LOCATION_INVALID" not in codes
    assert "INV_DEFAULT_LOCATION_MISSING_ENTITY" not in codes
