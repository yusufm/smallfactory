from __future__ import annotations
import os
import json
import subprocess
from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.validate import validate_repo


# -----------------
# Helpers
# -----------------


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
    init_git_repo(repo)

    # Touch entities file and commit WITHOUT ::sfid:: token
    _write(repo / "entities" / "p_x" / "entity.yml", "name: X\n")
    _git_commit_all(repo, "[test] touch entities without token")

    res = validate_repo(repo, include_git=True)
    assert any(i["code"] == "GIT_TOKEN_REQUIRED" for i in res["issues"])  # must include ::sfid:: token when touching PLM


def test_git_commit_token_present_allows_plm_commit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    # Use a core API that commits with ::sfid::<sfid> in the message
    from smallfactory.core.v1.entities import create_entity

    create_entity(repo, "p_ok", {"name": "Ok"})

    res = validate_repo(repo, include_git=True)
    codes = _codes(res["issues"])
    assert "GIT_TOKEN_REQUIRED" not in codes


def test_repo_upgrade_token_allows_plm_commit(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    _write(repo / "entities" / "p_up" / "entity.yml", "name: Up\n")
    _git_commit_all(repo, "[smallFactory] Repo format upgrade ::sf-op::repo-upgrade")

    res = validate_repo(repo, include_git=True)
    codes = _codes(res["issues"])
    assert "GIT_TOKEN_REQUIRED" not in codes


def test_entity_yml_invalid_yaml(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    # Invalid YAML (just a colon)
    _write(repo / "entities" / "p_bad" / "entity.yml", ":\n")
    res = validate_repo(repo, include_git=False)
    assert "ENT_ENTITY_YML_INVALID" in _codes(res["issues"])  # invalid yaml should be flagged


def test_events_jsonl_validation_for_build_entities(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "b_evt_1" / "entity.yml", "name: Build\n")
    _write(
        repo / "entities" / "b_evt_1" / "events.jsonl",
        "\n".join(
            [
                "not-json",
                json.dumps(["not-an-object"]),
                json.dumps({"message": "missing id"}),
                json.dumps({"id": "bad-id"}),
                json.dumps({"id": "evt_ok_1", "unknown": True}),
                json.dumps({"id": "evt_ok_2", "files": r"C:\\Windows\\System32\\cmd.exe"}),
                json.dumps({"id": "evt_ok_3", "files": ["../escape.txt"]}),
                json.dumps({"id": "evt_dup", "tags": ["note"]}),
                json.dumps({"id": "evt_dup", "tags": ["note2"]}),
            ]
        ) + "\n",
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    assert "ENT_EVENTS_JSON_INVALID" in codes
    assert "ENT_EVENTS_ID_MISSING_OR_INVALID" in codes
    assert "ENT_EVENTS_FIELD_UNKNOWN" in codes
    assert "ENT_EVENTS_FILES_PATH_INVALID" in codes
    assert "ENT_EVENTS_ID_DUPLICATE" in codes


def test_events_jsonl_disallowed_for_non_build_entities(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "p_evt_nope" / "entity.yml", "name: Part\n")
    _write(repo / "entities" / "p_evt_nope" / "events.jsonl", json.dumps({"id": "evt_1"}) + "\n")
    res = validate_repo(repo, include_git=False)
    assert "ENT_EVENTS_NOT_BUILD" in _codes(res["issues"])


def test_events_jsonl_valid_file_has_no_event_errors(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "b_evt_ok" / "entity.yml", "name: Build OK\n")
    _write(
        repo / "entities" / "b_evt_ok" / "events.jsonl",
        "\n".join(
            [
                json.dumps({"id": "evt_ok_1", "ts": "2026-02-28T00:00:00Z", "tags": ["repair"], "message": "ok"}),
                json.dumps({"id": "evt_ok_2", "files": ["event_attachments/evt_ok_2/scope.png"]}),
            ]
        ) + "\n",
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    assert "ENT_EVENTS_JSON_INVALID" not in codes
    assert "ENT_EVENTS_ID_MISSING_OR_INVALID" not in codes
    assert "ENT_EVENTS_FIELD_UNKNOWN" not in codes
    assert "ENT_EVENTS_FILES_PATH_INVALID" not in codes
    assert "ENT_EVENTS_NOT_BUILD" not in codes


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


def test_build_requires_part_sfid_and_forbids_top_part(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "p_widget" / "entity.yml", "name: Widget\n")

    # Missing part_sfid and using legacy top_part key is invalid.
    _write(
        repo / "entities" / "b_bad" / "entity.yml",
        "name: Bad Build\ntop_part: p_widget\n",
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    assert "ENT_BUILD_TOP_PART_FORBIDDEN" in codes
    assert "ENT_BUILD_PART_REQUIRED" in codes


def test_build_part_sfid_must_be_valid_part_entity(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "l_loc" / "entity.yml", "name: Location\n")

    _write(
        repo / "entities" / "b_invalid_1" / "entity.yml",
        "name: Build Bad 1\npart_sfid: bad-id\n",
    )
    _write(
        repo / "entities" / "b_invalid_2" / "entity.yml",
        "name: Build Bad 2\npart_sfid: l_loc\n",
    )
    _write(
        repo / "entities" / "b_invalid_3" / "entity.yml",
        "name: Build Bad 3\npart_sfid: p_missing\n",
    )
    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    assert "ENT_BUILD_PART_SFID_INVALID" in codes
    assert "ENT_BUILD_PART_NOT_PART" in codes
    assert "ENT_BUILD_PART_ENTITY_MISSING" in codes


def test_build_with_valid_part_sfid_has_no_build_field_errors(tmp_path: Path):
    repo = tmp_path / "repo"; repo.mkdir()
    _write(repo / "entities" / "p_widget" / "entity.yml", "name: Widget\n")
    _write(repo / "entities" / "b_ok" / "entity.yml", "name: Build OK\npart_sfid: p_widget\n")

    res = validate_repo(repo, include_git=False)
    codes = _codes(res["issues"])
    assert "ENT_BUILD_TOP_PART_FORBIDDEN" not in codes
    assert "ENT_BUILD_PART_REQUIRED" not in codes
    assert "ENT_BUILD_PART_SFID_INVALID" not in codes
    assert "ENT_BUILD_PART_NOT_PART" not in codes
    assert "ENT_BUILD_PART_ENTITY_MISSING" not in codes


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
