"""Tests for smallfactory.core.v1.config — SFID validation, config resolution,
env-var overrides, entity/inventory field-spec merging, and vision/sticker config."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from smallfactory.core.v1.config import (
    CONFIG_FILENAME,
    DATAREPO_CONFIG_FILENAME,
    INVENTORY_DEFAULT_FIELD_SPECS,
    ensure_config,
    get_datarepo_path,
    get_entities_specs,
    get_entity_field_specs_for_sfid,
    get_inventory_field_specs,
    get_ollama_base_url,
    get_openrouter_api_key,
    get_openrouter_base_url,
    get_stickers_default_fields,
    get_vision_model,
    get_vision_provider,
    load_config,
    load_datarepo_config,
    save_config,
    validate_sfid,
)


# ---------------------------------------------------------------------------
# validate_sfid — SPEC boundary conditions
# ---------------------------------------------------------------------------

class TestValidateSfid:
    """SFID must match ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$"""

    def test_valid_part(self):
        validate_sfid("p_m3x10")

    def test_valid_location(self):
        validate_sfid("l_inbox")

    def test_valid_with_hyphens_and_underscores(self):
        validate_sfid("p_foo-bar_baz1")

    def test_minimum_length_3(self):
        validate_sfid("p_x")  # 3 chars exactly

    def test_maximum_length_64(self):
        # prefix 'p_' + 62 alphanumerics = 64 chars
        validate_sfid("p_" + "a" * 62)

    def test_too_short_raises(self):
        with pytest.raises(ValueError):
            validate_sfid("p_")  # 2 chars — below minimum

    def test_too_long_raises(self):
        with pytest.raises(ValueError):
            validate_sfid("p_" + "a" * 63)  # 65 chars

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            validate_sfid("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            validate_sfid(None)  # type: ignore

    def test_integer_raises(self):
        with pytest.raises(ValueError):
            validate_sfid(42)  # type: ignore

    def test_uppercase_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("P_Hello")

    def test_no_prefix_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("nounderscore")

    def test_leading_digit_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("1p_bad")

    def test_trailing_hyphen_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("p_bad-")

    def test_spaces_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("p_ space")

    def test_dot_dot_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("..")

    def test_slash_rejected(self):
        with pytest.raises(ValueError):
            validate_sfid("p_foo/bar")


# ---------------------------------------------------------------------------
# Config file resolution & env-var overrides
# ---------------------------------------------------------------------------

class TestConfigResolution:
    """ensure_config / load_config / save_config honour SF_CONFIG_FILE,
    SF_CONFIG_DIR, and SF_DATA_PATH env vars."""

    def test_ensure_config_creates_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        result = ensure_config()
        assert result == cfg_file
        assert cfg_file.exists()
        data = yaml.safe_load(cfg_file.read_text())
        assert data["default_datarepo"] is None

    def test_ensure_config_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        ensure_config()
        # Write custom content
        cfg_file.write_text(yaml.safe_dump({"default_datarepo": "/some/path"}))
        # Second call must NOT overwrite
        ensure_config()
        data = yaml.safe_load(cfg_file.read_text())
        assert data["default_datarepo"] == "/some/path"

    def test_sf_config_dir_takes_precedence_over_cwd(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_CONFIG_FILE", raising=False)
        monkeypatch.setenv("SF_CONFIG_DIR", str(tmp_path))
        result = ensure_config()
        assert result == (tmp_path / CONFIG_FILENAME).resolve()

    def test_sf_data_path_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_CONFIG_FILE", raising=False)
        monkeypatch.delenv("SF_CONFIG_DIR", raising=False)
        monkeypatch.setenv("SF_DATA_PATH", str(tmp_path))
        result = ensure_config()
        assert result == (tmp_path / CONFIG_FILENAME).resolve()

    def test_load_and_save_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        ensure_config()
        save_config({"default_datarepo": "/test/repo", "extra": True})
        loaded = load_config()
        assert loaded["default_datarepo"] == "/test/repo"
        assert loaded["extra"] is True

    def test_get_datarepo_path_raises_when_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        ensure_config()
        with pytest.raises(RuntimeError, match="default_datarepo not set"):
            get_datarepo_path()

    def test_get_datarepo_path_resolves(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        repo = tmp_path / "myrepo"
        repo.mkdir()
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        save_config({"default_datarepo": str(repo)})
        assert get_datarepo_path() == repo.resolve()


# ---------------------------------------------------------------------------
# Datarepo config (sfdatarepo.yml)
# ---------------------------------------------------------------------------

class TestDatarepoConfig:

    def test_load_datarepo_config_missing_file(self, tmp_path: Path):
        assert load_datarepo_config(tmp_path) == {}

    def test_load_datarepo_config_reads_yaml(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(
            yaml.safe_dump({"smallfactory_version": "1.0", "inventory": {"fields": {"qty": {}}}})
        )
        cfg = load_datarepo_config(tmp_path)
        assert cfg["smallfactory_version"] == "1.0"
        assert "qty" in cfg["inventory"]["fields"]

    def test_load_datarepo_config_empty_yaml(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text("")
        assert load_datarepo_config(tmp_path) == {}


# ---------------------------------------------------------------------------
# Inventory field specs
# ---------------------------------------------------------------------------

class TestInventoryFieldSpecs:

    def test_defaults_when_no_datarepo_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        # Point load_datarepo_config at a dir with no sfdatarepo.yml
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        save_config({"default_datarepo": str(tmp_path)})
        specs = get_inventory_field_specs()
        assert specs == INVENTORY_DEFAULT_FIELD_SPECS

    def test_custom_fields_override_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cfg_file = tmp_path / CONFIG_FILENAME
        monkeypatch.setenv("SF_CONFIG_FILE", str(cfg_file))
        save_config({"default_datarepo": str(tmp_path)})
        custom = {"serial": {"required": True, "regex": "^[A-Z0-9]+$"}}
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(
            yaml.safe_dump({"inventory": {"fields": custom}})
        )
        specs = get_inventory_field_specs()
        assert "serial" in specs
        assert "quantity" not in specs  # custom replaces defaults entirely


# ---------------------------------------------------------------------------
# Entity field specs merging (global + per-type)
# ---------------------------------------------------------------------------

class TestEntityFieldSpecs:

    def test_no_specs_returns_empty(self, tmp_path: Path):
        assert get_entities_specs(tmp_path) == {"fields": {}, "types": {}}

    def test_global_fields_returned(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "entities": {"fields": {"name": {"required": True}}}
        }))
        specs = get_entities_specs(tmp_path)
        assert specs["fields"]["name"]["required"] is True

    def test_per_type_fields_merge_with_global(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "entities": {
                "fields": {"name": {"required": True}},
                "types": {"p": {"fields": {"mpn": {"required": False}}}},
            }
        }))
        merged = get_entity_field_specs_for_sfid("p_test", tmp_path)
        # Global 'name' should be present
        assert merged["name"]["required"] is True
        # Type-specific 'mpn' should be present
        assert "mpn" in merged

    def test_type_fields_override_global(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "entities": {
                "fields": {"name": {"required": True, "regex": "^.+$"}},
                "types": {"p": {"fields": {"name": {"required": False}}}},
            }
        }))
        merged = get_entity_field_specs_for_sfid("p_test", tmp_path)
        # Type override should win
        assert merged["name"]["required"] is False

    def test_location_type_prefix(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "entities": {
                "types": {"l": {"fields": {"zone": {"required": True}}}},
            }
        }))
        merged = get_entity_field_specs_for_sfid("l_warehouse", tmp_path)
        assert "zone" in merged

    def test_unknown_type_gets_only_global(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "entities": {
                "fields": {"name": {"required": True}},
                "types": {"p": {"fields": {"mpn": {}}}},
            }
        }))
        merged = get_entity_field_specs_for_sfid("x_custom", tmp_path)
        assert "name" in merged
        assert "mpn" not in merged


# ---------------------------------------------------------------------------
# Stickers config
# ---------------------------------------------------------------------------

class TestStickersConfig:

    def test_no_config_returns_empty(self, tmp_path: Path):
        assert get_stickers_default_fields(tmp_path) == []

    def test_valid_fields_returned(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "stickers": {"batch": {"default_fields": ["manufacturer", "value"]}}
        }))
        fields = get_stickers_default_fields(tmp_path)
        assert fields == ["manufacturer", "value"]

    def test_empty_strings_filtered(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "stickers": {"batch": {"default_fields": ["name", "", "  ", "mpn"]}}
        }))
        fields = get_stickers_default_fields(tmp_path)
        assert fields == ["name", "mpn"]

    def test_non_list_returns_empty(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "stickers": {"batch": {"default_fields": "not-a-list"}}
        }))
        assert get_stickers_default_fields(tmp_path) == []

    def test_missing_batch_key(self, tmp_path: Path):
        (tmp_path / DATAREPO_CONFIG_FILENAME).write_text(yaml.safe_dump({
            "stickers": {"other": True}
        }))
        assert get_stickers_default_fields(tmp_path) == []


# ---------------------------------------------------------------------------
# Vision / VLM env-var configuration
# ---------------------------------------------------------------------------

class TestVisionConfig:

    def test_ollama_base_url_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_OLLAMA_BASE_URL", raising=False)
        assert get_ollama_base_url() == "http://localhost:11434"

    def test_ollama_base_url_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_OLLAMA_BASE_URL", "http://gpu-box:11434")
        assert get_ollama_base_url() == "http://gpu-box:11434"

    def test_vision_model_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_VISION_MODEL", raising=False)
        assert get_vision_model() == "qwen2.5vl:3b"

    def test_vision_model_override(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_VISION_MODEL", "openai/gpt-4o-mini")
        assert get_vision_model() == "openai/gpt-4o-mini"

    def test_vision_provider_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_VISION_PROVIDER", raising=False)
        assert get_vision_provider() == "ollama"

    def test_vision_provider_openrouter(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_VISION_PROVIDER", "openrouter")
        assert get_vision_provider() == "openrouter"

    def test_vision_provider_invalid_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_VISION_PROVIDER", "invalid_provider")
        assert get_vision_provider() == "ollama"

    def test_vision_provider_case_insensitive(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_VISION_PROVIDER", "OpenRouter")
        assert get_vision_provider() == "openrouter"

    def test_openrouter_base_url_default(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_OPENROUTER_BASE_URL", raising=False)
        assert get_openrouter_base_url() == "https://openrouter.ai/api/v1"

    def test_openrouter_api_key_default_empty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("SF_OPENROUTER_API_KEY", raising=False)
        assert get_openrouter_api_key() == ""

    def test_openrouter_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("SF_OPENROUTER_API_KEY", "sk-test-123")
        assert get_openrouter_api_key() == "sk-test-123"
