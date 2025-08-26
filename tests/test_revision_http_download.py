from __future__ import annotations
import io
import tarfile
from pathlib import Path
import importlib.util

import pytest

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from smallfactory.core.v1.entities import create_entity, cut_revision

# Skip these tests entirely if Flask is not installed
pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


def _init_git_repo(root: Path) -> None:
    import subprocess
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Configure minimal identity for commits
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True)


def _import_web_app_module() -> object:
    web_app_path = Path(__file__).resolve().parents[1] / "web" / "app.py"
    spec = importlib.util.spec_from_file_location("sf_web_app", str(web_app_path))
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Make project root importable similar to app.py behavior
    sys.path.insert(0, str(web_app_path.parent.parent))
    spec.loader.exec_module(mod)  # type: ignore
    return mod


@pytest.fixture()
def web_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create temp git repo to act as datarepo
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _init_git_repo(repo)

    # Import the web app module
    mod = _import_web_app_module()

    # Point get_datarepo_path at our temp repo
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    # Disable autopush by default to keep tests deterministic
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")
    # Enable autocommit by default
    monkeypatch.setenv("SF_WEB_AUTOCOMMIT", "1")

    return mod


def test_revision_download_endpoint_serves_tar_gz(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()
    assert isinstance(repo, Path)

    sfid = "p_dl1"
    create_entity(repo, sfid, {"name": "Download Test"})

    # Prepare a draft revision '1' using core API and add a sample file
    cut_revision(repo, sfid, rev="1", notes="draft for download")
    sample_dir = repo / "entities" / sfid / "revisions" / "1"
    (sample_dir / "docs").mkdir(parents=True, exist_ok=True)
    (sample_dir / "docs" / "readme.txt").write_text("hello world\n")

    client = app.test_client()

    # Release the revision via HTTP
    r_rel = client.post(f"/api/entities/{sfid}/revisions/1/release", json={"notes": "release"})
    assert r_rel.status_code == 200
    assert r_rel.get_json().get("success") is True

    # Download archive
    r = client.get(f"/api/entities/{sfid}/revisions/1/download")
    assert r.status_code == 200

    # Headers
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert f"{sfid}_rev1.tar.gz" in cd
    # Some environments may set a variant of gzip type; accept common ones
    assert r.headers.get("Content-Type") in ("application/gzip", "application/x-gzip", "application/octet-stream")

    # Validate tar content and structure
    data = r.data
    bio = io.BytesIO(data)
    with tarfile.open(fileobj=bio, mode="r:gz") as tf:
        names = tf.getnames()
        # Archive root folder
        arc_root = f"{sfid}_rev1"
        assert any(n == arc_root or n.startswith(arc_root + "/") for n in names)
        # Sample file should be present under the root
        assert f"{arc_root}/docs/readme.txt" in names
        # And its content should be correct
        member = tf.getmember(f"{arc_root}/docs/readme.txt")
        fobj = tf.extractfile(member)
        assert fobj is not None
        assert fobj.read() == b"hello world\n"
