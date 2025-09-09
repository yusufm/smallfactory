import base64
import json
import re
from typing import Optional, Type

from pydantic import BaseModel, Field, ConfigDict

from .config import (
    get_ollama_base_url,
    get_vision_model,
    get_vision_provider,
    get_openrouter_base_url,
    get_openrouter_api_key,
)


class InvoicePart(BaseModel):
    """Structured fields extracted from an invoice to create/update a part.

    All fields are optional; unknowns should be returned as null by the model.
    """

    supplier_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None  # prefer ISO (YYYY-MM-DD) if present

    part_name: Optional[str] = None
    manufacturer: Optional[str] = None
    mpn: Optional[str] = None
    description: Optional[str] = None

    unit_price: Optional[float] = None
    currency: Optional[str] = None
    quantity: Optional[int] = None
    uom: Optional[str] = None

    location_l_sfid: Optional[str] = Field(default=None, description="Inventory location SFID, e.g. l_a1")

    notes: Optional[str] = None
    tags: Optional[list[str]] = None

    model_config = ConfigDict(extra="ignore")


def _build_schema_instruction() -> str:
    return (
        "Return a JSON object with exactly these keys: "
        "supplier_name, invoice_number, invoice_date, part_name, manufacturer, mpn, description, "
        "unit_price, currency, quantity, uom, location_l_sfid, notes, tags. "
        "Types: strings for text fields; unit_price as number; quantity as integer; tags as array of strings; "
        "use null when unknown. Do not include extra keys. Do not include any text outside the JSON."
    )


def _ensure_json_only(text: str) -> str:
    """Extract the first JSON object from a text response."""
    text = text.strip()
    # Fast path
    if text.startswith("{") and text.endswith("}"):
        return text
    # Try to find the first {...} block
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("Model did not return JSON")
    return m.group(0)


def ask_image(
    prompt: str,
    image_bytes: bytes,
    *,
    schema: Optional[Type[BaseModel]] = None,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.1,
) -> dict:
    """Send an image + prompt to the configured VLM provider.

    Providers: 'ollama' (default) and 'openrouter'.
    If schema is provided, enforce JSON-only output and validate via Pydantic before returning.
    Returns a dict: if schema is None => {"text": str, "model": str};
    else => {"data": dict, "model": str}.
    """
    provider = get_vision_provider()
    model_name = model or get_vision_model()

    if provider == "openrouter":
        # Lazy import to avoid hard dependency when not using OpenRouter
        try:
            import requests  # type: ignore
        except Exception as e:
            raise RuntimeError("Python 'requests' package not installed. Install with: pip install requests") from e
        # Use OpenAI-compatible Chat Completions API via OpenRouter
        api_key = (get_openrouter_api_key() or "").strip()
        if not api_key:
            raise RuntimeError("OpenRouter API key not set. Set SF_OPENROUTER_API_KEY.")
        base = base_url or get_openrouter_base_url()
        # Normalize URL
        url = base.rstrip("/") + "/chat/completions"

        img_b64 = base64.b64encode(image_bytes).decode("ascii")
        sys_msg = (
            "You are a precise information extraction engine. " + _build_schema_instruction()
            if schema is not None
            else "You are a helpful vision assistant. Answer concisely."
        )
        messages = [
            {"role": "system", "content": sys_msg},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                    },
                ],
            },
        ]
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model_name,
            "messages": messages,
            "temperature": float(temperature),
        }
        r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"OpenRouter error {r.status_code}: {r.text[:200]}")
        data = r.json() if r.text else {}
        content = (
            (data.get("choices") or [{}])[0]
            .get("message", {})
            .get("content", "")
            .strip()
        )
        if schema is None:
            return {"text": content, "model": model_name}
        json_text = _ensure_json_only(content)
        try:
            obj = json.loads(json_text)
        except Exception as e:
            raise ValueError(f"Failed to parse model JSON: {e}")
        try:
            parsed = schema.model_validate(obj)
        except Exception as e:
            raise ValueError(f"Response did not match expected schema: {e}")
        return {"data": parsed.model_dump(mode="python"), "model": model_name}

    # Default: Ollama provider
    try:
        import ollama
        from ollama import Client
    except Exception as e:
        raise RuntimeError("Ollama client is not installed. Install with: pip install ollama") from e

    host = base_url or get_ollama_base_url()
    client = Client(host=host)

    img_b64 = base64.b64encode(image_bytes).decode("ascii")

    messages: list = []
    if schema is not None:
        messages.append(
            {
                "role": "system",
                "content": (
                    "You are a precise information extraction engine. "
                    + _build_schema_instruction()
                ),
            }
        )
    else:
        messages.append(
            {
                "role": "system",
                "content": "You are a helpful vision assistant. Answer concisely.",
            }
        )

    messages.append(
        {
            "role": "user",
            "content": prompt,
            "images": [img_b64],
        }
    )

    resp = client.chat(
        model=model_name,
        messages=messages,
        options={"temperature": float(temperature)},
    )

    content = (resp or {}).get("message", {}).get("content", "").strip()

    if schema is None:
        return {"text": content, "model": model_name}

    # Enforce JSON only and validate
    json_text = _ensure_json_only(content)
    try:
        obj = json.loads(json_text)
    except Exception as e:
        raise ValueError(f"Failed to parse model JSON: {e}")

    try:
        parsed = schema.model_validate(obj)
    except Exception as e:
        raise ValueError(f"Response did not match expected schema: {e}")

    return {"data": parsed.model_dump(mode="python"), "model": model_name}


def extract_invoice_part(image_bytes: bytes) -> dict:
    """High-level helper to extract part fields from an invoice image.

    Returns {"data": <InvoicePart dict>, "model": <model>}.
    """
    user_prompt = (
        "From this invoice, extract the part/supplier fields. "
        "Prefer canonical values (e.g., manufacturer official name, clean MPN). "
        "If multiple line items exist, pick the most relevant hardware/electronic part."
    )
    return ask_image(user_prompt, image_bytes, schema=InvoicePart)
