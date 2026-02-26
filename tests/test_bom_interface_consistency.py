from __future__ import annotations

import importlib.util
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import (
    bom_add_line,
    create_entity,
    resolved_bom_tree,
    resolved_bom_view,
)
from smallfactory.core.v1.inventory import inventory_post

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _run_cli_json(monkeypatch: pytest.MonkeyPatch, sf_cli_mod, argv: list[str]):
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["sf", "--format", "json", *argv])
    with redirect_stdout(out), redirect_stderr(err):
        code = 0
        try:
            sf_cli_mod.main()
        except SystemExit as e:
            code = int(e.code or 0)
    if code != 0:
        raise AssertionError(f"CLI failed with code {code}: {err.getvalue() or out.getvalue()}")
    text = out.getvalue().strip()
    assert text, f"CLI produced no JSON output for argv={argv!r}"
    return json.loads(text)


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    web_mod = import_web_app_module()
    monkeypatch.setattr(web_mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    from smallfactory.cli import sf_cli

    monkeypatch.setattr(sf_cli, "get_datarepo_path", lambda: repo)
    return repo, web_mod, sf_cli


def test_cli_bom_ls_matches_api_bom_deep_nodes(env, monkeypatch: pytest.MonkeyPatch):
    repo, web_mod, sf_cli = env
    create_entity(repo, "l_main", {"name": "Main"})
    create_entity(repo, "p_root", {"name": "Root"})
    create_entity(repo, "p_mid", {"name": "Mid"})
    create_entity(repo, "p_leaf", {"name": "Leaf"})

    bom_add_line(repo, "p_root", use="p_mid", qty=2, rev="released")
    bom_add_line(repo, "p_mid", use="p_leaf", qty=3, rev="released")
    inventory_post(repo, "p_leaf", 4, location="l_main")

    client = web_mod.app.test_client()

    cli_nodes = _run_cli_json(monkeypatch, sf_cli, ["bom", "ls", "p_root"])
    api_nodes = (client.get("/api/entities/p_root/bom/deep").get_json() or {}).get("nodes")
    assert cli_nodes == api_nodes

    cli_nodes_d0 = _run_cli_json(monkeypatch, sf_cli, ["bom", "ls", "p_root", "--max-depth", "0"])
    api_nodes_d0 = (client.get("/api/entities/p_root/bom/deep?max_depth=0").get_json() or {}).get("nodes")
    assert cli_nodes_d0 == api_nodes_d0


def test_cli_bom_ls_delegates_to_core_read_model(env, monkeypatch: pytest.MonkeyPatch):
    _, _, sf_cli = env
    expected = [
        {
            "parent": "p_root",
            "use": "p_child",
            "name": "Child",
            "qty": 1,
            "rev": "released",
            "resolved_rev": "released",
            "level": 1,
            "is_alt": False,
            "alternates_group": None,
            "gross_qty": 1,
            "cycle": False,
            "onhand_total": 0,
        }
    ]
    monkeypatch.setattr(sf_cli, "ent_resolved_bom_view", lambda *a, **k: expected)
    out = _run_cli_json(monkeypatch, sf_cli, ["bom", "ls", "p_root"])
    assert out == expected


def test_core_bom_tree_level_contract_and_view_offset(env):
    repo, _, _ = env
    create_entity(repo, "p_root_lv", {"name": "Root"})
    create_entity(repo, "p_mid_lv", {"name": "Mid"})
    create_entity(repo, "p_leaf_lv", {"name": "Leaf"})

    bom_add_line(repo, "p_root_lv", use="p_mid_lv", qty=2, rev="released")
    bom_add_line(repo, "p_mid_lv", use="p_leaf_lv", qty=3, rev="released")

    tree = resolved_bom_tree(repo, "p_root_lv")
    view = resolved_bom_view(repo, "p_root_lv", level_offset=1)

    assert len(tree) == len(view)
    assert [n.get("use") for n in tree] == [n.get("use") for n in view]
    for t, v in zip(tree, view):
        assert int(v.get("level", -1)) == int(t.get("level", -1)) + 1

    immediate = resolved_bom_tree(repo, "p_root_lv", max_depth=0)
    assert {n.get("use") for n in immediate} == {"p_mid_lv"}
    assert all(int(n.get("level", -1)) == 0 for n in immediate)
