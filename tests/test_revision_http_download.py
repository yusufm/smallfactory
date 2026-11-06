from __future__ import annotations
import io
import tarfile
from pathlib import Path

import pytest

from conftest import init_git_repo, import_web_app_module
from smallfactory.core.v1.entities import create_entity, cut_revision

# Skip these tests entirely if Flask is not installed
pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


@pytest.fixture()
def web_mod(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # Create temp git repo to act as datarepo
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    init_git_repo(repo)

    # Import the web app module
    mod = import_web_app_module()

    # Point get_datarepo_path at our temp repo
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    # Disable autopush by default to keep tests deterministic
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

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


def test_revision_download_endpoint_accepts_custom_label(web_mod):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()
    assert isinstance(repo, Path)

    sfid = "p_dl_custom"
    create_entity(repo, sfid, {"name": "Download Custom Label"})

    cut_revision(repo, sfid, rev="A01", notes="draft custom")
    client = app.test_client()

    r_rel = client.post(f"/api/entities/{sfid}/revisions/A01/release", json={"notes": "release"})
    assert r_rel.status_code == 200
    assert r_rel.get_json().get("success") is True

    r = client.get(f"/api/entities/{sfid}/revisions/A01/download")
    assert r.status_code == 200
    cd = r.headers.get("Content-Disposition", "")
    assert "attachment" in cd
    assert f"{sfid}_revA01.tar.gz" in cd


@pytest.mark.parametrize("bad_rev", [".", "..", "rev/1", "rev\\1", "bad$rev"])
def test_cut_revision_rejects_dot_and_traversal_labels(web_mod, bad_rev):
    repo = web_mod.get_datarepo_path()
    sfid = "p_dl_sec"
    create_entity(repo, sfid, {"name": "Download Security Test"})
    with pytest.raises(ValueError):
        cut_revision(repo, sfid, rev=bad_rev, notes="bad rev")


@pytest.mark.parametrize("bad_rev", ["%2e", "%2e%2e", "..%20", "rev\\1", "bad$rev"])
def test_revision_endpoints_reject_dot_and_invalid_labels(web_mod, bad_rev):
    mod = web_mod
    app = mod.app
    repo = mod.get_datarepo_path()
    assert isinstance(repo, Path)

    sfid = "p_dl_sec_http"
    create_entity(repo, sfid, {"name": "Download Security HTTP Test"})

    client = app.test_client()

    r_dl = client.get(f"/api/entities/{sfid}/revisions/{bad_rev}/download")
    assert r_dl.status_code == 400
    assert r_dl.get_json().get("success") is False

    r_mf = client.get(f"/api/entities/{sfid}/revisions/{bad_rev}/manifest")
    assert r_mf.status_code in (400, 404)
    assert r_mf.get_json().get("success") is False


def test_revision_download_rejects_invalid_sfid(web_mod):
    client = web_mod.app.test_client()
    r = client.get("/api/entities/INVALID$SFID/revisions/1/download")
    assert r.status_code == 400
    assert r.get_json().get("success") is False
