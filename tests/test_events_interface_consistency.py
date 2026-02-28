from __future__ import annotations

import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import create_entity

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


def test_cli_events_ls_matches_api(env, monkeypatch: pytest.MonkeyPatch):
    repo, web_mod, sf_cli = env
    create_entity(repo, "b_evt_001", {"name": "Build EVT 001"})

    _run_cli_json(
        monkeypatch,
        sf_cli,
        ["entities", "events", "append", "b_evt_001", "--message", "first", "--tags", "repair,task_open"],
    )

    cli_events = _run_cli_json(monkeypatch, sf_cli, ["entities", "events", "ls", "b_evt_001"])
    api_events = (web_mod.app.test_client().get("/api/entities/b_evt_001/events").get_json() or {}).get("events")
    assert cli_events == api_events


def test_api_and_cli_event_mutations_are_consistent(env, monkeypatch: pytest.MonkeyPatch):
    repo, web_mod, sf_cli = env
    create_entity(repo, "b_evt_002", {"name": "Build EVT 002"})
    client = web_mod.app.test_client()

    created = client.post(
        "/api/entities/b_evt_002/events/append",
        json={"event": {"message": "before", "tags": ["measurement"]}},
    ).get_json() or {}
    event_id = ((created.get("event") or {}).get("id"))
    assert event_id

    _run_cli_json(
        monkeypatch,
        sf_cli,
        ["entities", "events", "update", "b_evt_002", event_id, "--message", "after"],
    )
    _run_cli_json(
        monkeypatch,
        sf_cli,
        ["entities", "events", "tags", "b_evt_002", event_id, "--tags", "repair,measurement"],
    )
    _run_cli_json(
        monkeypatch,
        sf_cli,
        [
            "entities",
            "events",
            "link-file",
            "b_evt_002",
            event_id,
            f"event_attachments/{event_id}/evidence.txt",
        ],
    )

    cli_events = _run_cli_json(monkeypatch, sf_cli, ["entities", "events", "ls", "b_evt_002"])
    api_events = (client.get("/api/entities/b_evt_002/events").get_json() or {}).get("events")
    assert cli_events == api_events
    assert cli_events[0]["message"] == "after"
    assert cli_events[0]["tags"] == ["repair", "measurement"]
    assert f"event_attachments/{event_id}/evidence.txt" in (cli_events[0].get("files") or [])


def test_cli_events_append_delegates_to_core(env, monkeypatch: pytest.MonkeyPatch):
    _, _, sf_cli = env
    expected = {
        "sfid": "b_evt_mock",
        "event": {"id": "evt_1", "ts": "2026-02-27T00:00:00+00:00", "tags": ["repair"], "message": "ok"},
        "events": [{"id": "evt_1", "ts": "2026-02-27T00:00:00+00:00", "tags": ["repair"], "message": "ok"}],
        "entity": {"sfid": "b_evt_mock", "name": "Mock Build"},
    }
    monkeypatch.setattr(sf_cli, "ent_get_entity", lambda *a, **k: {"sfid": "b_evt_mock"})
    monkeypatch.setattr(sf_cli, "ent_append_build_event", lambda *a, **k: expected)

    out = _run_cli_json(
        monkeypatch,
        sf_cli,
        ["entities", "events", "append", "b_evt_mock", "--message", "ok", "--tags", "repair"],
    )
    assert out == expected


def test_cli_append_supports_files_at_creation(env, monkeypatch: pytest.MonkeyPatch):
    repo, _, sf_cli = env
    create_entity(repo, "b_evt_003", {"name": "Build EVT 003"})

    created = _run_cli_json(
        monkeypatch,
        sf_cli,
        [
            "entities",
            "events",
            "append",
            "b_evt_003",
            "--message",
            "created_with_files",
            "--tags",
            "repair",
            "--file",
            "event_attachments/new/a.txt",
            "--file",
            "event_attachments/new/b.png",
        ],
    )

    files = (created.get("event") or {}).get("files") or []
    assert files == ["event_attachments/new/a.txt", "event_attachments/new/b.png"]


def test_cli_append_uploads_files_at_creation(env, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo, _, sf_cli = env
    create_entity(repo, "b_evt_004", {"name": "Build EVT 004"})

    src1 = tmp_path / "cap.txt"
    src2 = tmp_path / "scope.png"
    src1.write_text("capture-1", encoding="utf-8")
    src2.write_bytes(b"\x89PNG\r\n")

    created = _run_cli_json(
        monkeypatch,
        sf_cli,
        [
            "entities",
            "events",
            "append",
            "b_evt_004",
            "--message",
            "created_with_uploads",
            "--upload",
            str(src1),
            "--upload",
            str(src2),
        ],
    )

    event = created.get("event") or {}
    ev_id = event.get("id")
    files = event.get("files") or []
    assert ev_id
    assert len(files) == 2
    assert all(str(p).startswith(f"event_attachments/{ev_id}/") for p in files)
    for rel in files:
        assert (repo / "entities" / "b_evt_004" / "files" / rel).exists()
