from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from conftest import import_web_app_module, init_git_repo
from smallfactory.core.v1.entities import create_entity, get_entity
from smallfactory.core.v1.locks import repo_process_lock, upgrade_in_progress_marker
from smallfactory.core.v1.repo import write_datarepo_config

pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "sf.py", "-R", str(repo), "--format", "json", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


@pytest.fixture()
def web_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)
    write_datarepo_config(repo)
    create_entity(repo, "p_widget", {"name": "Widget"})

    mod = import_web_app_module()
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")
    mod.app.config["TESTING"] = True
    return repo, mod


def test_cli_and_web_mutations_wait_on_same_shared_repo_lock(web_env):
    repo, mod = web_env
    web_started = threading.Event()
    web_result: dict[str, object] = {}

    def web_worker() -> None:
        web_started.set()
        with mod.app.test_client() as client:
            resp = client.post("/api/entities/p_widget/update", json={"category": "electrical"})
            web_result["status_code"] = resp.status_code
            web_result["json"] = resp.get_json()

    with repo_process_lock(repo, timeout_seconds=1.0, poll_interval_seconds=0.01):
        thread = threading.Thread(target=web_worker, daemon=True)
        thread.start()
        assert web_started.wait(timeout=1.0) is True

        cli = subprocess.Popen(
            [
                sys.executable,
                "sf.py",
                "-R",
                str(repo),
                "--format",
                "json",
                "entities",
                "set",
                "p_widget",
                "serialnumber=SN123",
            ],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        time.sleep(0.25)
        assert thread.is_alive() is True
        assert cli.poll() is None

    thread.join(timeout=5.0)
    assert thread.is_alive() is False
    cli_out, cli_err = cli.communicate(timeout=5.0)
    assert cli.returncode == 0, cli_err or cli_out

    assert web_result["status_code"] == 200
    assert (web_result["json"] or {}).get("success") is True

    entity = get_entity(repo, "p_widget")
    assert entity["category"] == "electrical"
    assert entity["serialnumber"] == "SN123"


def test_cli_and_web_mutations_are_blocked_during_upgrade(web_env):
    repo, mod = web_env

    with upgrade_in_progress_marker(repo):
        cli = _run_cli(repo, "entities", "set", "p_widget", "serialnumber=SN999")
        assert cli.returncode != 0
        assert "upgrade in progress" in (cli.stderr or cli.stdout).lower()

        with mod.app.test_client() as client:
            resp = client.post("/api/entities/p_widget/update", json={"category": "blocked"})

        assert resp.status_code == 400
        payload = resp.get_json() or {}
        assert payload.get("success") is False
        assert payload.get("error") == "Repository upgrade in progress; retry after upgrade completes."

    entity = get_entity(repo, "p_widget")
    assert entity.get("category") != "blocked"
    assert entity.get("serialnumber") != "SN999"
