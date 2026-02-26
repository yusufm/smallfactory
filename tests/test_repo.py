"""Tests for smallfactory.core.v1.repo — repo initialisation, datarepo config
scaffolding, default location setup, and create_or_clone."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import init_git_repo
from smallfactory.core.v1.config import DATAREPO_CONFIG_FILENAME, CONFIG_FILENAME
from smallfactory.core.v1.repo import (
    create_or_clone,
    init_local_repo,
    initial_commit_and_optional_push,
    scaffold_default_location,
    set_default_datarepo,
    set_remote,
    write_datarepo_config,
)


# ---------------------------------------------------------------------------
# init_local_repo
# ---------------------------------------------------------------------------

class TestInitLocalRepo:

    def test_creates_directory_and_git_repo(self, tmp_path: Path):
        repo = tmp_path / "new_repo"
        result = init_local_repo(repo)
        assert result.exists()
        assert (result / ".git").is_dir()

    def test_idempotent_on_existing_dir(self, tmp_path: Path):
        repo = tmp_path / "existing"
        repo.mkdir()
        result = init_local_repo(repo)
        assert result.exists()
        assert (result / ".git").is_dir()

    def test_returns_resolved_path(self, tmp_path: Path):
        repo = tmp_path / "sub" / "repo"
        result = init_local_repo(repo)
        assert result == repo.resolve()


# ---------------------------------------------------------------------------
# write_datarepo_config
# ---------------------------------------------------------------------------

class TestWriteDatarepoConfig:

    def test_creates_sfdatarepo_yml(self, tmp_path: Path):
        init_git_repo(tmp_path)
        config_file = write_datarepo_config(tmp_path)
        assert config_file.exists()
        assert config_file.name == DATAREPO_CONFIG_FILENAME

    def test_config_contains_version_and_inventory(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        data = yaml.safe_load((tmp_path / DATAREPO_CONFIG_FILENAME).read_text())
        assert "smallfactory_version" in data
        assert "inventory" in data
        assert "fields" in data["inventory"]

    def test_config_contains_entity_type_specs(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        data = yaml.safe_load((tmp_path / DATAREPO_CONFIG_FILENAME).read_text())
        assert "entities" in data
        assert "p" in data["entities"]["types"]
        assert "name" in data["entities"]["types"]["p"]["fields"]

    def test_creates_inventory_and_entities_dirs(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        assert (tmp_path / "inventory").is_dir()
        assert (tmp_path / "entities").is_dir()

    def test_creates_gitattributes_with_union_merge(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        gia = tmp_path / ".gitattributes"
        assert gia.exists()
        content = gia.read_text()
        assert "journal.ndjson merge=union" in content

    def test_gitattributes_idempotent(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        write_datarepo_config(tmp_path)
        content = (tmp_path / ".gitattributes").read_text()
        # Should not duplicate the union merge line
        assert content.count("journal.ndjson merge=union") == 1


# ---------------------------------------------------------------------------
# scaffold_default_location
# ---------------------------------------------------------------------------

class TestScaffoldDefaultLocation:

    def test_creates_location_entity_and_updates_config(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        # Make an initial commit so git is happy
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

        scaffold_default_location(tmp_path, "l_inbox")

        ent = tmp_path / "entities" / "l_inbox" / "entity.yml"
        assert ent.exists()
        assert "name:" in ent.read_text()

        dr = yaml.safe_load((tmp_path / DATAREPO_CONFIG_FILENAME).read_text())
        assert dr["inventory"]["default_location"] == "l_inbox"

    def test_idempotent(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

        scaffold_default_location(tmp_path, "l_inbox")
        # Second call should be a no-op (nothing to commit)
        scaffold_default_location(tmp_path, "l_inbox")

    def test_custom_location_sfid(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

        scaffold_default_location(tmp_path, "l_warehouse")
        ent = tmp_path / "entities" / "l_warehouse" / "entity.yml"
        assert ent.exists()

    def test_invalid_sfid_raises(self, tmp_path: Path):
        with pytest.raises(ValueError, match="Invalid"):
            scaffold_default_location(tmp_path, "BAD SFID")


# ---------------------------------------------------------------------------
# set_remote / set_default_datarepo
# ---------------------------------------------------------------------------

class TestSetRemote:

    def test_adds_origin(self, tmp_path: Path):
        init_git_repo(tmp_path)
        set_remote(tmp_path, "https://github.com/test/repo.git")
        r = subprocess.run(["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True)
        assert "origin" in r.stdout
        assert "github.com" in r.stdout

    def test_empty_url_is_noop(self, tmp_path: Path):
        init_git_repo(tmp_path)
        set_remote(tmp_path, "")
        r = subprocess.run(["git", "remote", "-v"], cwd=tmp_path, capture_output=True, text=True)
        assert "origin" not in r.stdout


class TestSetDefaultDatarepo:

    def test_writes_to_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        repo = tmp_path / "datarepo"
        repo.mkdir()
        set_default_datarepo(repo)
        data = yaml.safe_load(cfg_file.read_text())
        assert data["default_datarepo"] == str(repo)


# ---------------------------------------------------------------------------
# initial_commit_and_optional_push
# ---------------------------------------------------------------------------

class TestInitialCommit:

    def test_commits_config_file(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        initial_commit_and_optional_push(tmp_path, has_remote=False)
        r = subprocess.run(
            ["git", "log", "--oneline"], cwd=tmp_path, capture_output=True, text=True
        )
        assert "Initial smallFactory" in r.stdout

    def test_no_push_without_remote(self, tmp_path: Path):
        init_git_repo(tmp_path)
        write_datarepo_config(tmp_path)
        # Should not raise even though there's no remote
        initial_commit_and_optional_push(tmp_path, has_remote=False)


# ---------------------------------------------------------------------------
# create_or_clone (local path only — cloning requires network)
# ---------------------------------------------------------------------------

class TestCreateOrClone:

    def test_local_repo_creation(self, tmp_path: Path):
        repo = tmp_path / "new"
        result = create_or_clone(repo, None)
        assert result.exists()
        assert (result / ".git").is_dir()
