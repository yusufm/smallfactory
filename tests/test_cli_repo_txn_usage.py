from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.repo import write_datarepo_config


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
    write_datarepo_config(repo)

    from smallfactory.cli import sf_cli

    monkeypatch.setattr(sf_cli, "get_datarepo_path", lambda: repo)
    return repo, sf_cli


def _install_repo_txn_probe(monkeypatch: pytest.MonkeyPatch, sf_cli, repo: Path):
    calls: list[Path] = []

    def fake_run_repo_mutation(path, mutate_fn, **kwargs):
        calls.append(path)
        return mutate_fn()

    monkeypatch.setattr(sf_cli, "run_repo_mutation", fake_run_repo_mutation)
    return calls


def test_cli_inventory_post_uses_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)
    monkeypatch.setattr(
        sf_cli,
        "inventory_post",
        lambda *a, **k: {"part": "p_widget", "location": "l_inbox", "qty_delta": 3, "txn": "txn_1"},
    )

    out = _run_cli_json(
        monkeypatch,
        sf_cli,
        ["inventory", "post", "--part", "p_widget", "--qty-delta", "+3", "--l_sfid", "l_inbox"],
    )

    assert calls == [repo]
    assert out["txn"] == "txn_1"


def test_cli_inventory_onhand_write_uses_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)
    monkeypatch.setattr(
        sf_cli,
        "inventory_onhand",
        lambda *a, **k: {"uom": "ea", "as_of": "2026-04-05T00:00:00Z", "by_location": {"l_inbox": 3}, "total": 3},
    )

    out = _run_cli_json(monkeypatch, sf_cli, ["inventory", "onhand", "--part", "p_widget"])

    assert calls == [repo]
    assert out["total"] == 3


def test_cli_events_append_uploads_inside_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo, sf_cli = env
    calls: list[Path] = []
    in_txn = {"active": False}

    src = tmp_path / "capture.txt"
    src.write_text("capture", encoding="utf-8")

    def fake_run_repo_mutation(path, mutate_fn, **kwargs):
        calls.append(path)
        in_txn["active"] = True
        try:
            return mutate_fn()
        finally:
            in_txn["active"] = False

    def fake_upload(*args, **kwargs):
        assert in_txn["active"] is True
        return {"path": "event_attachments/evt_1/capture.txt"}

    def fake_append(*args, **kwargs):
        assert in_txn["active"] is True
        return {
            "sfid": "b_evt_mock",
            "event": {
                "id": "evt_1",
                "ts": "2026-04-05T00:00:00Z",
                "message": "ok",
                "files": ["event_attachments/evt_1/capture.txt"],
            },
        }

    monkeypatch.setattr(sf_cli, "run_repo_mutation", fake_run_repo_mutation)
    monkeypatch.setattr(sf_cli, "ent_get_entity", lambda *a, **k: {"sfid": "b_evt_mock", "events": []})
    monkeypatch.setattr(sf_cli, "f_upload_file", fake_upload)
    monkeypatch.setattr(sf_cli, "ent_append_build_event", fake_append)

    out = _run_cli_json(
        monkeypatch,
        sf_cli,
        ["entities", "events", "append", "b_evt_mock", "--message", "ok", "--upload", str(src)],
    )

    assert calls == [repo]
    assert (out.get("event") or {}).get("files") == ["event_attachments/evt_1/capture.txt"]


@pytest.mark.parametrize(
    ("argv", "patch_attr", "patch_result", "expected_key"),
    [
        (["inventory", "rebuild"], "inventory_rebuild", {"parts": ["p_widget"], "locations": ["l_inbox"]}, "parts"),
        (["entities", "set", "p_widget", "name=Updated"], "ent_update_entity_fields", {"sfid": "p_widget", "name": "Updated"}, "name"),
        (["entities", "retire", "p_widget", "--reason", "obsolete"], "ent_retire_entity", {"sfid": "p_widget", "retired": True}, "retired"),
        (["entities", "build", "serial", "b_2026_0001", "SN123"], "ent_update_entity_fields", {"sfid": "b_2026_0001", "serialnumber": "SN123"}, "serialnumber"),
        (
            ["entities", "build", "datetime", "b_2026_0001", "2026-04-05T12:00:00Z"],
            "ent_update_entity_fields",
            {"sfid": "b_2026_0001", "datetime": "2026-04-05T12:00:00Z"},
            "datetime",
        ),
        (["entities", "files", "mkdir", "p_widget", "reports"], "f_mkdir", {"path": "reports"}, "path"),
        (["entities", "events", "update", "b_evt_001", "evt_1", "--message", "updated"], "ent_update_build_event", {"event": {"id": "evt_1"}}, "event"),
        (["entities", "events", "tags", "b_evt_001", "evt_1", "--tags", "qa,pass"], "ent_update_build_event_tags", {"event": {"tags": ["qa", "pass"]}}, "event"),
        (
            ["entities", "events", "link-file", "b_evt_001", "evt_1", "event_attachments/evt_1/log.txt"],
            "ent_add_build_event_file_link",
            {"event": {"files": ["event_attachments/evt_1/log.txt"]}},
            "event",
        ),
        (["entities", "revision", "new", "p_widget", "A"], "ent_cut_revision", {"sfid": "p_widget", "rev": "A"}, "rev"),
        (["entities", "revision", "release", "p_widget", "A"], "ent_release_revision", {"sfid": "p_widget", "rev": "A"}, "rev"),
        (["bom", "add", "p_parent", "--use", "p_child"], "ent_bom_add_line", {"index": 0, "line": {"use": "p_child"}}, "index"),
        (["bom", "rm", "p_parent", "--use", "p_child"], "ent_bom_remove_line", {"removed": 1}, "removed"),
        (["bom", "set", "p_parent", "--index", "0", "--qty", "2"], "ent_bom_set_line", {"line": {"qty": 2}}, "line"),
        (["bom", "alt-add", "p_parent", "--index", "0", "--use", "p_alt"], "ent_bom_alt_add", {"line": {"alternates": [{"use": "p_alt"}]}}, "line"),
        (["bom", "alt-rm", "p_parent", "--index", "0", "--alt-use", "p_alt"], "ent_bom_alt_remove", {"line": {"alternates": []}}, "line"),
    ],
)
def test_cli_mutators_use_shared_repo_txn(
    env,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    patch_attr: str,
    patch_result: dict,
    expected_key: str,
):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)

    if argv[:4] == ["entities", "events", "update", "b_evt_001"]:
        monkeypatch.setattr(sf_cli, "ent_get_entity", lambda *a, **k: {"sfid": "b_evt_001", "events": []})

    monkeypatch.setattr(sf_cli, patch_attr, lambda *a, **k: patch_result)

    out = _run_cli_json(monkeypatch, sf_cli, argv)

    assert calls == [repo]
    assert expected_key in out


def test_cli_files_add_uses_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)
    src = tmp_path / "capture.txt"
    src.write_text("capture", encoding="utf-8")

    monkeypatch.setattr(sf_cli, "f_upload_file", lambda *a, **k: {"path": "capture.txt"})

    out = _run_cli_json(monkeypatch, sf_cli, ["entities", "files", "add", "p_widget", str(src), "capture.txt"])

    assert calls == [repo]
    assert out["path"] == "capture.txt"


def test_cli_files_move_dir_uses_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)

    monkeypatch.setattr(sf_cli, "f_move_dir", lambda *a, **k: {"src": "old", "dst": "new"})

    out = _run_cli_json(monkeypatch, sf_cli, ["entities", "files", "mv", "p_widget", "old", "new", "--dir"])

    assert calls == [repo]
    assert out["dst"] == "new"


def test_cli_revision_bump_wraps_bump_and_release_inside_one_shared_repo_txn(env, monkeypatch: pytest.MonkeyPatch):
    repo, sf_cli = env
    calls = _install_repo_txn_probe(monkeypatch, sf_cli, repo)
    in_txn = {"active": False}

    def fake_run_repo_mutation(path, mutate_fn, **kwargs):
        calls.append(path)
        in_txn["active"] = True
        try:
            return mutate_fn()
        finally:
            in_txn["active"] = False

    def fake_bump(*args, **kwargs):
        assert in_txn["active"] is True
        return {"new_rev": "B"}

    def fake_release(*args, **kwargs):
        assert in_txn["active"] is True
        return {"sfid": "p_widget", "rev": "B"}

    monkeypatch.setattr(sf_cli, "run_repo_mutation", fake_run_repo_mutation)
    monkeypatch.setattr(sf_cli, "ent_bump_revision", fake_bump)
    monkeypatch.setattr(sf_cli, "ent_release_revision", fake_release)

    out = _run_cli_json(monkeypatch, sf_cli, ["entities", "revision", "bump", "p_widget"])

    assert calls == [repo]
    assert out["rev"] == "B"
