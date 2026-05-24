from __future__ import annotations

from pathlib import Path
import json
import subprocess

import pytest
import yaml

from conftest import init_git_repo
from smallfactory.core.v1 import entities as entities_mod
from smallfactory.core.v1.entities import (
    add_build_event_file_link,
    append_build_event,
    create_entity,
    delete_entity,
    get_entity,
    retire_entity,
    update_build_event,
    update_build_event_tags,
    update_entity_field,
    update_entity_fields,
)
from smallfactory.core.v1.config import validate_sfid


def test_create_part_entity_scaffolds_revision_and_refs_dirs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    created = create_entity(repo, "p_widget", {"name": "Widget"})
    assert created["sfid"] == "p_widget"

    ent_dir = repo / "entities" / "p_widget"
    assert (ent_dir / "entity.yml").exists()
    assert (ent_dir / "revisions" / ".gitkeep").exists()
    assert (ent_dir / "refs" / ".gitkeep").exists()
    # files/ is intentionally lazy-created by file APIs.
    assert not (ent_dir / "files").exists()

    on_disk = yaml.safe_load((ent_dir / "entity.yml").read_text()) or {}
    assert "sfid" not in on_disk


def test_entity_specs_are_enforced_on_create_and_update(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    (repo / "sfdatarepo.yml").write_text(
        (
            "entities:\n"
            "  fields:\n"
            "    name:\n"
            "      required: true\n"
            "  types:\n"
            "    p:\n"
            "      fields:\n"
            "        mpn:\n"
            "          regex: '^[A-Z0-9-]+$'\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Missing required field: name"):
        create_entity(repo, "p_bad", {"mpn": "ABC-1"})

    create_entity(repo, "p_good", {"name": "Good Part", "mpn": "ABC-1"})

    with pytest.raises(ValueError, match="does not match regex"):
        update_entity_field(repo, "p_good", "mpn", "bad value")

    with pytest.raises(ValueError, match="Cannot update 'sfid'"):
        update_entity_fields(repo, "p_good", {"sfid": "p_other"})

    updated = update_entity_fields(repo, "p_good", {"name": "Renamed Part", "mpn": "XYZ-9"})
    assert updated["name"] == "Renamed Part"
    assert updated["mpn"] == "XYZ-9"

    stored = get_entity(repo, "p_good")
    assert stored["name"] == "Renamed Part"
    assert stored["mpn"] == "XYZ-9"


def test_retire_sets_metadata_and_hard_delete_is_disallowed(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "p_legacy", {"name": "Legacy"})

    with pytest.raises(RuntimeError, match="Hard delete of entities is disabled"):
        delete_entity(repo, "p_legacy")

    retired = retire_entity(
        repo,
        "p_legacy",
        reason="replaced by p_new",
        retired_at="2026-01-01T00:00:00Z",
    )
    assert retired["retired"] is True
    assert retired["retired_at"] == "2026-01-01T00:00:00Z"
    assert retired["retired_reason"] == "replaced by p_new"

    stored = get_entity(repo, "p_legacy")
    assert stored["retired"] is True
    assert stored["retired_at"] == "2026-01-01T00:00:00Z"
    assert stored["retired_reason"] == "replaced by p_new"


def test_build_events_append(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_001", {"name": "Build Widget 001"})

    out = append_build_event(
        repo,
        "b_widget_001",
        {"tags": ["repair_request"], "message": "No USB enum"},
    )
    ev = out["event"]
    assert ev["tags"] == ["repair_request"]
    assert ev["id"]
    validate_sfid(ev["id"])
    assert len(out["events"]) == 1

    ent = get_entity(repo, "b_widget_001")
    assert isinstance(ent.get("events"), list)
    assert len(ent["events"]) == 1
    ent_yaml = yaml.safe_load((repo / "entities" / "b_widget_001" / "entity.yml").read_text(encoding="utf-8")) or {}
    assert "events" not in ent_yaml
    events_path = repo / "entities" / "b_widget_001" / "events.jsonl"
    assert events_path.exists()
    lines = [ln for ln in events_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    on_disk = json.loads(lines[0])
    assert on_disk["id"] == ev["id"]


def test_build_events_do_not_read_from_entity_yaml(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_005", {"name": "Build Widget 005"})
    ent_fp = repo / "entities" / "b_widget_005" / "entity.yml"
    raw = yaml.safe_load(ent_fp.read_text(encoding="utf-8")) or {}
    raw["events"] = [{"id": "evt_legacy", "tags": ["legacy"], "message": "legacy in yaml"}]
    ent_fp.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")

    ent = get_entity(repo, "b_widget_005")
    assert ent.get("events") == []


def test_build_events_read_rejects_missing_id(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_008", {"name": "Build Widget 008"})
    events_fp = repo / "entities" / "b_widget_008" / "events.jsonl"
    events_fp.write_text('{"message":"missing id","tags":["note"]}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Event field 'id' is required"):
        _ = get_entity(repo, "b_widget_008")


def test_write_yaml_preserves_existing_file_when_dump_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    target = tmp_path / "entity.yml"
    target.write_text("name: original\n", encoding="utf-8")

    def _boom(data, stream, sort_keys=False):
        stream.write("name: partial")
        raise RuntimeError("boom")

    monkeypatch.setattr(entities_mod.yaml, "safe_dump", _boom)

    with pytest.raises(RuntimeError, match="boom"):
        entities_mod._write_yaml(target, {"name": "updated"})

    assert target.read_text(encoding="utf-8") == "name: original\n"
    assert [p.name for p in target.parent.iterdir()] == ["entity.yml"]


def test_build_events_read_skips_malformed_json_lines(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_010", {"name": "Build Widget 010"})
    events_fp = repo / "entities" / "b_widget_010" / "events.jsonl"
    events_fp.write_text(
        '{"id":"evt_ok","tags":["note"],"message":"ok"}\n'
        '{not valid json}\n',
        encoding="utf-8",
    )

    ent = get_entity(repo, "b_widget_010")
    events = ent.get("events") or []
    assert len(events) == 1
    assert events[0]["id"] == "evt_ok"


def test_build_events_reject_non_build_entities(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "p_widget", {"name": "Widget"})

    with pytest.raises(ValueError, match="only supported for build entities"):
        append_build_event(repo, "p_widget", {"tags": ["log"], "message": "x"})

    with pytest.raises(ValueError, match="only supported for build entities"):
        update_build_event_tags(repo, "p_widget", "evt_1", ["note"])


def test_build_event_tags_optional_and_editable(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_002", {"name": "Build Widget 002"})
    out = append_build_event(repo, "b_widget_002", {"message": "generic note without tags"})
    ev = out["event"]
    assert ev["tags"] == []
    assert ev["id"]

    out2 = update_build_event_tags(repo, "b_widget_002", ev["id"], ["qa_observation", "retest"])
    assert out2["event"]["tags"] == ["qa_observation", "retest"]


def test_build_event_file_link(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_003", {"name": "Build Widget 003"})
    out = append_build_event(repo, "b_widget_003", {"message": "event with file"})
    ev = out["event"]

    out2 = add_build_event_file_link(repo, "b_widget_003", ev["id"], "event_attachments/test/photo1.jpg")
    files = out2["event"].get("files") or []
    assert isinstance(files, list)
    assert "event_attachments/test/photo1.jpg" in files


def test_build_event_file_link_rejects_windows_absolute_paths(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_011", {"name": "Build Widget 011"})
    out = append_build_event(repo, "b_widget_011", {"message": "event with file"})
    ev = out["event"]

    with pytest.raises(ValueError, match="relative to files"):
        add_build_event_file_link(repo, "b_widget_011", ev["id"], r"C:\Windows\System32\cmd.exe")


def test_build_event_full_update(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_004", {"name": "Build Widget 004"})
    out = append_build_event(repo, "b_widget_004", {"tags": ["note"], "message": "before"})
    ev = out["event"]

    out2 = update_build_event(
        repo,
        "b_widget_004",
        ev["id"],
        {
            "tags": ["qa_review"],
            "message": "after",
            "files": ["event_attachments/test/a.txt", "event_attachments/test/b.txt"],
        },
    )
    updated = out2["event"]
    assert updated["id"] == ev["id"]
    assert updated["tags"] == ["qa_review"]
    assert updated["message"] == "after"
    assert updated["files"] == ["event_attachments/test/a.txt", "event_attachments/test/b.txt"]


def test_build_event_rejects_unknown_fields(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_006", {"name": "Build Widget 006"})
    with pytest.raises(ValueError, match="Unsupported event field"):
        append_build_event(repo, "b_widget_006", {"message": "x", "target": "p_uut"})


def test_build_event_rejects_invalid_id(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    create_entity(repo, "b_widget_007", {"name": "Build Widget 007"})
    with pytest.raises(ValueError, match="sfid must match"):
        append_build_event(repo, "b_widget_007", {"id": "bad-id", "message": "x"})


def test_event_mutations_do_not_rewrite_entity_yaml(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    create_entity(repo, "b_widget_009", {"name": "Build Widget 009"})

    ent_fp = repo / "entities" / "b_widget_009" / "entity.yml"
    ent_before = ent_fp.read_text(encoding="utf-8")
    events_fp = repo / "entities" / "b_widget_009" / "events.jsonl"

    out1 = append_build_event(repo, "b_widget_009", {"message": "one", "tags": ["note"]})
    ev_id = out1["event"]["id"]
    assert ent_fp.read_text(encoding="utf-8") == ent_before
    show1 = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "entities/b_widget_009/events.jsonl" in show1
    assert "entities/b_widget_009/entity.yml" not in show1

    update_build_event_tags(repo, "b_widget_009", ev_id, ["repair"])
    assert ent_fp.read_text(encoding="utf-8") == ent_before
    show2 = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "entities/b_widget_009/events.jsonl" in show2
    assert "entities/b_widget_009/entity.yml" not in show2

    update_build_event(
        repo,
        "b_widget_009",
        ev_id,
        {"message": "two", "tags": ["repair"], "files": ["event_attachments/two.txt"]},
    )
    assert ent_fp.read_text(encoding="utf-8") == ent_before
    show3 = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "entities/b_widget_009/events.jsonl" in show3
    assert "entities/b_widget_009/entity.yml" not in show3

    add_build_event_file_link(repo, "b_widget_009", ev_id, "event_attachments/three.txt")
    assert ent_fp.read_text(encoding="utf-8") == ent_before
    show4 = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    assert "entities/b_widget_009/events.jsonl" in show4
    assert "entities/b_widget_009/entity.yml" not in show4
    assert events_fp.exists()


def test_create_entity_temp_file_is_cleaned_on_dump_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    def _boom(*args, **kwargs):
        raise RuntimeError("dump failed")

    monkeypatch.setattr(entities_mod.yaml, "safe_dump", _boom)
    with pytest.raises(RuntimeError, match="dump failed"):
        create_entity(repo, "p_tmp_cleanup", {"name": "Tmp Cleanup"})

    ent_dir = repo / "entities" / "p_tmp_cleanup"
    if ent_dir.exists():
        leaked = [p for p in ent_dir.iterdir() if p.is_file() and p.name.startswith("tmp")]
        assert leaked == []
