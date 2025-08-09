from __future__ import annotations

import base64
import io
from pathlib import Path
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


def compose_sticker_image(
    entity: dict,
    *,
    code_type: str = "qr",
    fields: Optional[Iterable[str]] = None,
    sticker_size: Tuple[int, int] = (600, 300),
    padding: int = 16,
) -> Image.Image:
    """Compose a printable sticker PNG for an entity.

    - code_type: 'qr'
    - fields: list of field names to render as human-readable text (besides SFID/name)
    - sticker_size: (width, height) in pixels (default 600x300 ≈ 2x1 in @ 300 DPI)
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

    # Fonts
    try:
        # If system has a TTF, you can point to it. Fallback to default.
        title_font = ImageFont.load_default()
        normal_font = ImageFont.load_default()
        mono_font = ImageFont.load_default()
    except Exception:
        title_font = ImageFont.load_default()
        normal_font = ImageFont.load_default()
        mono_font = ImageFont.load_default()

    y = padding

    # Title (name)
    title_lines = _wrap_text(draw, str(name), title_font, text_w)
    for ln in title_lines:
        draw.text((text_x, y), ln, fill=(0, 0, 0), font=title_font)
        y += title_font.getbbox(ln)[3]

    # SFID (monospace-ish)
    y += 4
    draw.text((text_x, y), f"SFID: {sfid}", fill=(0, 0, 0), font=mono_font)
    y += mono_font.getbbox("Hg")[3] + 8

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
                y += normal_font.getbbox(ln)[3]
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
) -> dict:
    """Generate a sticker for the given entity SFID.

    Returns dict with keys: 'sfid', 'code_type', 'fields', 'png_base64', 'filename'.
    Default size is 600x300 pixels (≈2x1 in @ 300 DPI).
    """
    ent = get_entity(datarepo_path, sfid)
    img = compose_sticker_image(ent, code_type="qr", fields=fields, sticker_size=size)
    b64 = image_to_base64_png(img, dpi=dpi)
    fname = f"sticker_{sfid}_qr.png"
    return {
        "sfid": sfid,
        "code_type": "qr",
        "fields": list(fields) if fields else [],
        "png_base64": b64,
        "filename": fname,
    }
