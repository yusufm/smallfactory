from __future__ import annotations

import importlib.util
import io
import json
import subprocess
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import bom_add_line, create_entity
from smallfactory.core.v1.inventory import inventory_post

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


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
    _init_git_repo(repo)

    web_mod = _import_web_app_module()
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
