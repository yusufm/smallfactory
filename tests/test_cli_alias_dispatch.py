from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.entities import bom_add_line, create_entity
from smallfactory.core.v1.repo import write_datarepo_config


def _run_cli(monkeypatch: pytest.MonkeyPatch, sf_cli_mod, argv: list[str], *, fmt: str = "json"):
    out = io.StringIO()
    err = io.StringIO()
    monkeypatch.setattr(sys, "argv", ["sf", "--format", fmt, *argv])
    with redirect_stdout(out), redirect_stderr(err):
        code = 0
        try:
            sf_cli_mod.main()
        except SystemExit as e:
            code = int(e.code or 0)
    return code, out.getvalue(), err.getvalue()


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    write_datarepo_config(repo)
    create_entity(repo, "p_widget", {"name": "Widget"})
    create_entity(repo, "p_parent", {"name": "Parent"})
    create_entity(repo, "p_child", {"name": "Child"})
    bom_add_line(repo, "p_parent", use="p_child", qty=1, rev="released")

    from smallfactory.cli import sf_cli

    monkeypatch.setattr(sf_cli, "get_datarepo_path", lambda: repo)
    return repo, sf_cli


def test_entities_list_alias_dispatches(env, monkeypatch: pytest.MonkeyPatch):
    _, sf_cli = env
    code, out, err = _run_cli(monkeypatch, sf_cli, ["entities", "list"])
    assert code == 0, err or out
    rows = json.loads(out)
    assert any(row.get("sfid") == "p_widget" for row in rows)


def test_entities_view_alias_dispatches(env, monkeypatch: pytest.MonkeyPatch):
    _, sf_cli = env
    code, out, err = _run_cli(monkeypatch, sf_cli, ["entities", "view", "p_widget"])
    assert code == 0, err or out
    entity = json.loads(out)
    assert entity.get("sfid") == "p_widget"


def test_bom_list_alias_dispatches(env, monkeypatch: pytest.MonkeyPatch):
    _, sf_cli = env
    code, out, err = _run_cli(monkeypatch, sf_cli, ["bom", "list", "p_parent"])
    assert code == 0, err or out
    rows = json.loads(out)
    assert any(row.get("use") == "p_child" for row in rows)


def test_bom_remove_alias_dispatches(env, monkeypatch: pytest.MonkeyPatch):
    repo, sf_cli = env
    code, out, err = _run_cli(monkeypatch, sf_cli, ["bom", "remove", "p_parent", "--use", "p_child"])
    assert code == 0, err or out
    payload = json.loads(out)
    assert payload.get("removed") == [0]

    code, out, err = _run_cli(monkeypatch, sf_cli, ["bom", "list", "p_parent"])
    assert code == 0, err or out
    assert json.loads(out) == []
