from __future__ import annotations
import io
from pathlib import Path
import importlib.util

import pytest
from PIL import Image

# Ensure project root on sys.path so 'smallfactory' is importable when running pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


# Skip these tests entirely if Flask is not installed
pytest.importorskip("flask", reason="Flask not installed; web API tests skipped")


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
    # Import the web app module
    mod = _import_web_app_module()

    # Point get_datarepo_path at a temp dir (not used by vision tests but keeps consistency)
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mod, "get_datarepo_path", lambda: repo)

    # Keep network/push side effects off by default
    monkeypatch.setenv("SF_WEB_AUTOPUSH", "0")

    return mod


def _jpeg_bytes(size=(32, 32), color=(200, 100, 50)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _png_bytes(size=(32, 32), color=(10, 200, 20)) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", size, color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_vision_extract_part_success_png_reencode_and_passthrough(monkeypatch: pytest.MonkeyPatch, web_mod):
    mod = web_mod
    app = mod.app

    captured = {"bytes": None}

    def fake_extract(img_bytes: bytes):
        captured["bytes"] = img_bytes
        return {"data": {"manufacturer": "Acme", "mpn": "AC-123"}, "model": "dummy"}

    # Monkeypatch the VLM extraction to avoid calling Ollama
    monkeypatch.setattr(mod, "vlm_extract_invoice_part", lambda b: fake_extract(b))

    client = app.test_client()

    # Provide a JPEG; server should convert to PNG before passing to extractor
    jpeg = _jpeg_bytes()
    data = {
        "file": (io.BytesIO(jpeg), "invoice.jpg", "image/jpeg"),
    }
    resp = client.post("/api/vision/extract/part", data=data, content_type="multipart/form-data")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body.get("success") is True
    assert body.get("result", {}).get("data", {}).get("manufacturer") == "Acme"

    # Extractor should have received PNG bytes (check magic header)
    assert captured["bytes"] is not None
    assert captured["bytes"].startswith(b"\x89PNG\r\n\x1a\n")
    # And PIL should be able to open it as PNG
    img = Image.open(io.BytesIO(captured["bytes"]))
    assert img.format == "PNG"


def test_vision_extract_part_missing_file_400(web_mod):
    mod = web_mod
    app = mod.app
    client = app.test_client()

    resp = client.post("/api/vision/extract/part", data={}, content_type="multipart/form-data")
    assert resp.status_code == 400
    data = resp.get_json()
    assert data.get("success") is False
    assert "No image file uploaded" in (data.get("error") or "")


def test_vision_extract_part_unsupported_type_400(web_mod):
    mod = web_mod
    app = mod.app
    client = app.test_client()

    data = {
        "file": (io.BytesIO(b"not-an-image"), "note.txt", "text/plain"),
    }
    resp = client.post("/api/vision/extract/part", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("success") is False
    assert "Unsupported file type" in (body.get("error") or "")


def test_vision_extract_part_oversize_400(web_mod):
    mod = web_mod
    app = mod.app
    client = app.test_client()

    # Create a >10MB payload (10*1024*1024 + 1)
    big = b"\x00" * (10 * 1024 * 1024 + 1)
    data = {
        "file": (io.BytesIO(big), "big.png", "image/png"),
    }
    resp = client.post("/api/vision/extract/part", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("success") is False
    assert "Image too large" in (body.get("error") or "")


def test_vision_extract_part_exception_returns_hint_500(monkeypatch: pytest.MonkeyPatch, web_mod):
    mod = web_mod
    app = mod.app

    def boom(_bytes: bytes):
        raise RuntimeError("Ollama client is not installed")

    monkeypatch.setattr(mod, "vlm_extract_invoice_part", boom)

    client = app.test_client()
    data = {
        "file": (io.BytesIO(_png_bytes()), "invoice.png", "image/png"),
    }
    resp = client.post("/api/vision/extract/part", data=data, content_type="multipart/form-data")
    assert resp.status_code == 500
    body = resp.get_json()
    assert body.get("success") is False
    assert "hint" in body
    assert "Ollama" in (body.get("hint") or "")


def test_vision_ask_missing_prompt_400(monkeypatch: pytest.MonkeyPatch, web_mod):
    mod = web_mod
    app = mod.app

    # Ensure the ask path does not try to reach Ollama by returning early due to missing prompt
    client = app.test_client()
    data = {
        "file": (io.BytesIO(_png_bytes()), "img.png", "image/png"),
    }
    resp = client.post("/api/vision/ask", data=data, content_type="multipart/form-data")
    assert resp.status_code == 400
    body = resp.get_json()
    assert body.get("success") is False
    assert "Missing prompt" in (body.get("error") or "")
