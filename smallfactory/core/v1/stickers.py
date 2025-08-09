from __future__ import annotations

import base64
import io
from pathlib import Path
import os
from typing import Iterable, List, Optional, Tuple

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore

# Optional deps
try:
    import qrcode
except Exception:  # pragma: no cover
    qrcode = None

from .entities import get_entity


def check_dependencies() -> dict:
    """Return availability of optional sticker dependencies.

    Keys:
      - qrcode: bool
    """
    return {
        "qrcode": qrcode is not None,
        "pillow": Image is not None,
    }


def _ensure_pillow():
    if Image is None:
        raise RuntimeError("pillow is not installed")


def _make_qr(data: str, box_size: int = 8, border: int = 2) -> Image.Image:
    _ensure_pillow()
    if qrcode is None:
        raise RuntimeError("qrcode package not installed")
    qr = qrcode.QRCode(version=None, error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=box_size, border=border)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    # qrcode returns PilImage or RGB — normalize to RGB Image
    if not isinstance(img, Image.Image):
        img = img.get_image()
    return img.convert("RGB")


# DataMatrix support removed — QR only.


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> List[str]:
    words = (text or "").split()
    lines: List[str] = []
    cur = ""
    for w in words:
        tmp = (cur + (" " if cur else "") + w).strip()
        w_, h_ = draw.textbbox((0, 0), tmp, font=font)[2:]
        if w_ <= max_width:
            cur = tmp
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if not lines:
        lines = [text or ""]
    return lines


def _try_load_font(candidates: List[str], size: int):
    """Try loading a TrueType/collection font from a list of filenames/paths.

    Returns a PIL ImageFont if successful, else None.
    """
    for path in candidates:
        try:
            if not path:
                continue
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return None


def _get_fonts(base_sz: int):
    """Load title/normal/mono fonts with the requested size, trying common locations.

    Honors optional env vars:
      - SMALLFACTORY_FONT: path or font name for proportional font
      - SMALLFACTORY_MONO_FONT: path or font name for monospace font
    """
    # Common font candidates across Linux/macOS
    proportional_candidates = [
        os.environ.get("SMALLFACTORY_FONT"),
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Helvetica.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    mono_candidates = [
        os.environ.get("SMALLFACTORY_MONO_FONT"),
        "DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Menlo.ttc",
        "/Library/Fonts/Courier New.ttf",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
    ]

    # Try to load proportional font
    normal = _try_load_font(proportional_candidates, base_sz)
    title = _try_load_font(proportional_candidates, int(round(base_sz * 1.2))) if normal is not None else None
    # Try to load mono font; if not found, reuse proportional as fallback to keep sizing consistent
    mono = _try_load_font(mono_candidates, base_sz) or (
        _try_load_font(proportional_candidates, base_sz)
    )

    if normal is None or title is None or mono is None:
        # Final fallback: bitmap default (fixed size). Text size won't scale with this font.
        # We still return defaults to avoid crashes.
        try:
            if title is None:
                title = ImageFont.load_default()
            if normal is None:
                normal = ImageFont.load_default()
            if mono is None:
                mono = ImageFont.load_default()
        except Exception:
            # In practice load_default() should always work if PIL is present
            title = normal = mono = ImageFont.load_default()

    return title, normal, mono


def compose_sticker_image(
    entity: dict,
    *,
    code_type: str = "qr",
    fields: Optional[Iterable[str]] = None,
    sticker_size: Tuple[int, int] = (600, 300),
    padding: int = 16,
    text_size: int = 24,
) -> Image.Image:
    """Compose a printable sticker PNG for an entity.

    - code_type: 'qr'
    - fields: list of field names to render as human-readable text (besides SFID/name)
    - sticker_size: (width, height) in pixels (default 600x300 ≈ 2x1 in @ 300 DPI)
    - text_size: base text size in pixels for normal/mono text. Title is ~1.2x this size.
    """
    _ensure_pillow()
    if code_type.lower() != "qr":
        raise ValueError("Only 'qr' is supported for code_type")
    sfid = entity.get("sfid", "")
    name = entity.get("name", sfid)

    width, height = sticker_size
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    # Generate code image (QR only)
    payload = sfid
    code_img = _make_qr(payload, box_size=8, border=2)

    # Scale code to fit left column (roughly square area)
    code_max = min(height - 2 * padding, width // 2 - 2 * padding)
    code_img = code_img.resize((code_max, code_max), Image.NEAREST)
    img.paste(code_img, (padding, (height - code_max) // 2))

    # Text area
    text_x = padding + code_max + padding
    text_w = width - text_x - padding

    # Fonts (attempt TrueType from common locations; fallback to default bitmap font)
    base_sz = max(8, int(text_size))
    title_font, normal_font, mono_font = _get_fonts(base_sz)

    y = padding

    # Title (name)
    title_lines = _wrap_text(draw, str(name), title_font, text_w)
    for ln in title_lines:
        draw.text((text_x, y), ln, fill=(0, 0, 0), font=title_font)
        tb = draw.textbbox((0, 0), ln, font=title_font)
        y += (tb[3] - tb[1])

    # SFID (monospace-ish)
    y += 4
    sfid_line = f"SFID: {sfid}"
    draw.text((text_x, y), sfid_line, fill=(0, 0, 0), font=mono_font)
    tb = draw.textbbox((0, 0), sfid_line, font=mono_font)
    y += (tb[3] - tb[1]) + 8

    # Additional fields
    if fields:
        for f in fields:
            if f in ("sfid", "name"):
                continue
            val = entity.get(f)
            if val is None:
                continue
            label = f"{f}: {val}"
            for ln in _wrap_text(draw, str(label), normal_font, text_w):
                draw.text((text_x, y), ln, fill=(0, 0, 0), font=normal_font)
                tb = draw.textbbox((0, 0), ln, font=normal_font)
                y += (tb[3] - tb[1])
            y += 2

    # Footer brand
    footer = "smallFactory"
    fw, fh = draw.textbbox((0, 0), footer, font=normal_font)[2:]
    draw.text((width - padding - fw, height - padding - fh), footer, fill=(120, 120, 120), font=normal_font)

    return img


def image_to_base64_png(img: Image.Image, dpi: Optional[int] = None) -> str:
    _ensure_pillow()
    bio = io.BytesIO()
    save_kwargs = {"format": "PNG"}
    # Embed DPI so printers honor physical dimensions (pHYs chunk in PNG)
    if dpi and dpi > 0:
        save_kwargs["dpi"] = (dpi, dpi)
    img.save(bio, **save_kwargs)
    return base64.b64encode(bio.getvalue()).decode("ascii")


def generate_sticker_for_entity(
    datarepo_path: Path,
    sfid: str,
    *,
    code_type: str = "qr",
    fields: Optional[Iterable[str]] = None,
    size: Tuple[int, int] = (600, 300),
    dpi: int = 300,
    text_size: int = 24,
) -> dict:
    """Generate a sticker for the given entity SFID.

    Returns dict with keys: 'sfid', 'code_type', 'fields', 'png_base64', 'filename'.
    Default size is 600x300 pixels (≈2x1 in @ 300 DPI).
    """
    ent = get_entity(datarepo_path, sfid)
    img = compose_sticker_image(ent, code_type="qr", fields=fields, sticker_size=size, text_size=text_size)
    b64 = image_to_base64_png(img, dpi=dpi)
    fname = f"sticker_{sfid}_qr.png"
    return {
        "sfid": sfid,
        "code_type": "qr",
        "fields": list(fields) if fields else [],
        "png_base64": b64,
        "filename": fname,
    }
