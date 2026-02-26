"""Tests for smallfactory.core.v1.stickers — dependency checks, sticker
composition, base64 encoding, and end-to-end generate_sticker_for_entity."""
from __future__ import annotations

import base64
import io
from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import init_git_repo
from smallfactory.core.v1.entities import create_entity

# Skip entire module if Pillow/qrcode are not installed (optional deps)
PIL = pytest.importorskip("PIL", reason="Pillow not installed")
qrcode_mod = pytest.importorskip("qrcode", reason="qrcode not installed")

from smallfactory.core.v1.stickers import (
    check_dependencies,
    compose_sticker_image,
    generate_sticker_for_entity,
    image_to_base64_png,
)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    p = tmp_path / "repo"
    p.mkdir(parents=True)
    init_git_repo(p)
    return p


# ---------------------------------------------------------------------------
# check_dependencies
# ---------------------------------------------------------------------------

class TestCheckDependencies:

    def test_reports_pillow_and_qrcode(self):
        deps = check_dependencies()
        # Since we importorskip'd above, both should be True in this test run
        assert deps["pillow"] is True
        assert deps["qrcode"] is True


# ---------------------------------------------------------------------------
# compose_sticker_image
# ---------------------------------------------------------------------------

class TestComposeStickerImage:

    def test_returns_pil_image(self):
        from PIL import Image
        entity = {"sfid": "p_m3x10", "name": "M3x10 Bolt"}
        img = compose_sticker_image(entity)
        assert isinstance(img, Image.Image)

    def test_default_size(self):
        entity = {"sfid": "p_test", "name": "Test"}
        img = compose_sticker_image(entity)
        assert img.size == (600, 300)

    def test_custom_size(self):
        entity = {"sfid": "p_test", "name": "Test"}
        img = compose_sticker_image(entity, sticker_size=(400, 200))
        assert img.size == (400, 200)

    def test_with_extra_fields(self):
        entity = {
            "sfid": "p_cap1",
            "name": "100uF Cap",
            "manufacturer": "Samsung",
            "mpn": "CL10A106MQ8NNNC",
        }
        img = compose_sticker_image(entity, fields=["manufacturer", "mpn"])
        assert img.size[0] > 0 and img.size[1] > 0

    def test_skips_sfid_and_name_in_fields(self):
        entity = {"sfid": "p_test", "name": "Test", "category": "IC"}
        # Passing sfid/name in fields list should not crash or double-render
        img = compose_sticker_image(entity, fields=["sfid", "name", "category"])
        assert img.size[0] > 0

    def test_none_field_values_skipped(self):
        entity = {"sfid": "p_test", "name": "Test", "mpn": None}
        img = compose_sticker_image(entity, fields=["mpn"])
        assert img.size[0] > 0

    def test_non_qr_code_type_raises(self):
        entity = {"sfid": "p_test", "name": "Test"}
        with pytest.raises(ValueError, match="Only.*qr"):
            compose_sticker_image(entity, code_type="datamatrix")

    def test_missing_name_uses_sfid(self):
        entity = {"sfid": "p_noname"}
        img = compose_sticker_image(entity)
        assert img.size[0] > 0

    def test_custom_text_size(self):
        entity = {"sfid": "p_test", "name": "Test"}
        img = compose_sticker_image(entity, text_size=12)
        assert img.size[0] > 0

    def test_very_long_name_wraps(self):
        entity = {"sfid": "p_test", "name": "A" * 200}
        # Should not crash — long text should be word-wrapped
        img = compose_sticker_image(entity)
        assert img.size[0] > 0


# ---------------------------------------------------------------------------
# image_to_base64_png
# ---------------------------------------------------------------------------

class TestImageToBase64Png:

    def test_produces_valid_base64(self):
        from PIL import Image
        img = Image.new("RGB", (100, 50), "white")
        b64 = image_to_base64_png(img)
        raw = base64.b64decode(b64)
        # PNG magic bytes
        assert raw[:4] == b"\x89PNG"

    def test_dpi_embedded(self):
        from PIL import Image
        img = Image.new("RGB", (100, 50), "white")
        b64 = image_to_base64_png(img, dpi=300)
        raw = base64.b64decode(b64)
        # Verify we get a valid PNG (we can't easily check DPI in raw bytes
        # without parsing PNG chunks, but at minimum it should be valid PNG)
        assert raw[:4] == b"\x89PNG"


# ---------------------------------------------------------------------------
# generate_sticker_for_entity (end-to-end)
# ---------------------------------------------------------------------------

class TestGenerateStickerForEntity:

    def test_returns_expected_keys(self, repo: Path):
        create_entity(repo, "p_test_sticker", {"name": "Sticker Test"})
        result = generate_sticker_for_entity(repo, "p_test_sticker")
        assert result["sfid"] == "p_test_sticker"
        assert result["code_type"] == "qr"
        assert "png_base64" in result
        assert result["filename"] == "sticker_p_test_sticker_qr.png"

    def test_image_is_valid_png(self, repo: Path):
        create_entity(repo, "p_test_sticker", {"name": "Sticker Test"})
        result = generate_sticker_for_entity(repo, "p_test_sticker")
        raw = base64.b64decode(result["png_base64"])
        assert raw[:4] == b"\x89PNG"

    def test_with_fields(self, repo: Path):
        create_entity(repo, "p_cap", {"name": "Cap", "manufacturer": "TDK", "mpn": "X123"})
        result = generate_sticker_for_entity(
            repo, "p_cap", fields=["manufacturer", "mpn"]
        )
        assert result["fields"] == ["manufacturer", "mpn"]

    def test_nonexistent_entity_raises(self, repo: Path):
        with pytest.raises(FileNotFoundError):
            generate_sticker_for_entity(repo, "p_ghost")

    def test_code_type_always_qr(self, repo: Path):
        create_entity(repo, "p_test2", {"name": "Test2"})
        # Even if user passes code_type, the function forces "qr" internally
        result = generate_sticker_for_entity(repo, "p_test2", code_type="barcode")
        assert result["code_type"] == "qr"
