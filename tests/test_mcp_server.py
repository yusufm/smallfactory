from __future__ import annotations

from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.entities import append_build_event, create_entity
from smallfactory.core.v1.inventory import inventory_post
from smallfactory.mcp_server import (
    _analytics_query_impl,
    _collect_build_events,
    _entities_search_impl,
    _inventory_onhand_with_zero_parts,
    _paginate_list,
    _parts_inventory_rows,
    _result,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    init_git_repo(root)
    return root


def test_collect_build_events_filters_by_part_field(repo: Path):
    create_entity(repo, "p_widget", {"name": "Widget"})
    create_entity(repo, "p_case", {"name": "Case"})

    create_entity(repo, "b_widget_1", {"name": "Build Widget", "part_sfid": "p_widget"})
    create_entity(repo, "b_case_1", {"name": "Build Case", "part_sfid": "p_case"})

    append_build_event(repo, "b_widget_1", {"message": "repair A", "tags": ["repair"]})
    append_build_event(repo, "b_case_1", {"message": "repair B", "tags": ["repair"]})

    events_for_widget = _collect_build_events(repo, part_sfid="p_widget")
    assert len(events_for_widget) == 1
    assert events_for_widget[0]["build_sfid"] == "b_widget_1"
    assert events_for_widget[0]["part_sfid"] == "p_widget"

    events_for_case = _collect_build_events(repo, part_sfid="p_case")
    assert len(events_for_case) == 1
    assert events_for_case[0]["build_sfid"] == "b_case_1"
    assert events_for_case[0]["part_sfid"] == "p_case"


def test_analytics_query_groups_by_part(repo: Path):
    create_entity(repo, "p_motor", {"name": "Motor"})
    create_entity(repo, "b_motor_1", {"name": "Build 1", "part_sfid": "p_motor"})
    create_entity(repo, "b_motor_2", {"name": "Build 2", "part_sfid": "p_motor"})

    append_build_event(repo, "b_motor_1", {"message": "repair 1", "tags": ["repair"]})
    append_build_event(repo, "b_motor_2", {"message": "repair 2", "tags": ["repair"]})

    out = _analytics_query_impl(repo, group_by="part_sfid")
    assert out["group_by"] == "part_sfid"
    assert out["rows"][0]["key"] == "p_motor"
    assert out["rows"][0]["count"] == 2


def test_entities_search_filters_type_and_tags(repo: Path):
    create_entity(repo, "p_alpha", {"name": "Alpha", "tags": ["repair"]})
    create_entity(repo, "l_shelf_1", {"name": "Shelf", "tags": ["storage"]})

    out = _entities_search_impl(repo, query="alpha", type_prefix="p", tags=["repair"])
    assert out["count"] == 1
    assert out["results"][0]["sfid"] == "p_alpha"
    assert out["next_cursor"] == ""


def test_inventory_summary_includes_zero_parts(repo: Path):
    create_entity(repo, "l_inbox", {"name": "Inbox"})
    create_entity(repo, "p_stocked", {"name": "Stocked", "uom": "ea"})
    create_entity(repo, "p_zero", {"name": "Zero", "uom": "ea"})
    inventory_post(repo, "p_stocked", 5, l_sfid="l_inbox")

    out = _inventory_onhand_with_zero_parts(
        repo,
        part_sfid=None,
        location_sfid=None,
        include_zero_parts=True,
    )
    rows = out.get("parts") or []
    by_id = {r["sfid"]: r for r in rows}
    assert by_id["p_stocked"]["total"] == 5
    assert by_id["p_zero"]["total"] == 0
    assert out["parts_count"] == 2


def test_inventory_location_includes_zero_parts(repo: Path):
    create_entity(repo, "l_a1", {"name": "A1"})
    create_entity(repo, "p_a", {"name": "Part A"})
    create_entity(repo, "p_b", {"name": "Part B"})
    inventory_post(repo, "p_a", 3, l_sfid="l_a1")

    out = _inventory_onhand_with_zero_parts(
        repo,
        part_sfid=None,
        location_sfid="l_a1",
        include_zero_parts=True,
    )
    parts = out.get("parts") or {}
    assert parts["p_a"] == 3
    assert parts["p_b"] == 0
    assert out["parts_count"] == 2


def test_entities_search_paginates_with_cursor(repo: Path):
    create_entity(repo, "p_a1", {"name": "A1"})
    create_entity(repo, "p_a2", {"name": "A2"})
    create_entity(repo, "p_a3", {"name": "A3"})

    page1 = _entities_search_impl(repo, query="p_a", type_prefix="p", limit=2)
    assert page1["count"] == 2
    assert page1["next_cursor"] == "2"

    page2 = _entities_search_impl(repo, query="p_a", type_prefix="p", limit=2, cursor=page1["next_cursor"])
    assert page2["count"] == 1
    assert page2["next_cursor"] == ""


def test_build_events_list_style_cursor_pagination(repo: Path):
    create_entity(repo, "p_evt", {"name": "Evt"})
    create_entity(repo, "b_evt_1", {"name": "Build Evt", "part_sfid": "p_evt"})
    append_build_event(repo, "b_evt_1", {"message": "m1", "tags": ["repair"]})
    append_build_event(repo, "b_evt_1", {"message": "m2", "tags": ["repair"]})
    append_build_event(repo, "b_evt_1", {"message": "m3", "tags": ["repair"]})

    events = _collect_build_events(repo, build_sfid="b_evt_1")
    assert len(events) == 3
    page1 = _paginate_list(events, limit=2, cursor=None)
    assert page1["count"] == 2
    assert page1["next_cursor"] == "2"
    page2 = _paginate_list(events, limit=2, cursor=page1["next_cursor"])
    assert page2["count"] == 1
    assert page2["next_cursor"] == ""


def test_parts_inventory_rows_return_full_table(repo: Path):
    create_entity(repo, "l_main", {"name": "Main"})
    create_entity(repo, "p_r1", {"name": "R1"})
    create_entity(repo, "p_r2", {"name": "R2"})
    inventory_post(repo, "p_r1", 7, l_sfid="l_main")

    rows = _parts_inventory_rows(repo)
    by_id = {r["sfid"]: r for r in rows}
    assert by_id["p_r1"]["qty"] == 7
    assert by_id["p_r2"]["qty"] == 0


def test_result_envelope_adds_schema_version():
    out = _result({"a": 1})
    assert out["a"] == 1
    assert out["schema_version"] == "1.1.0"
