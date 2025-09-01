import pathlib
import yaml
import re
import os

SF_TOOL_VERSION = "1.0"
CONFIG_FILENAME = ".smallfactory.yml"
DATAREPO_CONFIG_FILENAME = "sfdatarepo.yml"

# -------------------------------
# Inventory field schema (YAML-driven)
# -------------------------------
# We store the inventory field specs in .smallfactory.yml under:
# inventory:
#   fields:
#     <field_name>: { required: <bool>, regex: <pattern>, description: <text> }
# Unknown fields are allowed at runtime; only known fields have validation.

# Defaults used to bootstrap a fresh config file and as fallback if missing.
INVENTORY_DEFAULT_FIELD_SPECS = {
    # Only required field for inventory files per SPEC: quantity (non-negative integer).
    # Other keys may be present and should be ignored by readers and preserved by writers.
    "quantity": {"required": True, "regex": r"^[0-9]+$", "description": "On-hand quantity as non-negative integer."},
}


# -------------------------------
# sfid validation (SPEC)
# -------------------------------
# Authoritative pattern: ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$
SFID_REGEX: str = r"^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$"


def validate_sfid(sfid: str) -> None:
    """Validate that an sfid conforms to SPEC and is safe as a file/dir name.

    Raises ValueError if invalid.
    """
    if not isinstance(sfid, str) or not sfid:
        raise ValueError("sfid is required")
    if re.fullmatch(SFID_REGEX, sfid) is None:
        raise ValueError(
            "sfid must match ^(?=.{3,64}$)[a-z]+_[a-z0-9_-]*[a-z0-9]$ and be lowercase"
        )


def _resolve_config_path() -> pathlib.Path:
    """Resolve the path to .smallfactory.yml with environment overrides.

    Precedence:
      1) SF_CONFIG_FILE = absolute or relative path to the config file
      2) SF_CONFIG_DIR = directory containing the config file
      3) SF_DATA_PATH  = parent data path (config at $SF_DATA_PATH/.smallfactory.yml)
      4) Fallback to CWD: ./ .smallfactory.yml (backward compatible)
    """
    env_file = os.environ.get("SF_CONFIG_FILE")
    if env_file:
        return pathlib.Path(env_file).expanduser().resolve()
    env_dir = os.environ.get("SF_CONFIG_DIR") or os.environ.get("SF_DATA_PATH")
    if env_dir:
        return pathlib.Path(env_dir).expanduser().resolve() / CONFIG_FILENAME
    return pathlib.Path(CONFIG_FILENAME).expanduser().resolve()


def ensure_config() -> pathlib.Path:
    """Ensure local .smallfactory.yml exists; create with defaults if missing.

    Returns the path to the config file.
    """
    config_path = _resolve_config_path()
    if not config_path.exists():
        # Ensure parent directory exists when using SF_CONFIG_DIR / SF_DATA_PATH
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        config = {
            "default_datarepo": None,
        }
        with open(config_path, "w") as f:
            yaml.safe_dump(config, f)
    return config_path


def load_config() -> dict:
    config_path = ensure_config()
    with open(config_path) as f:
        return yaml.safe_load(f) or {}


def save_config(config: dict) -> None:
    config_path = _resolve_config_path()
    # Ensure parent directory exists in case of custom location
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)


def get_datarepo_path() -> pathlib.Path:
    config = load_config()
    datarepo = config.get("default_datarepo")
    if not datarepo:
        raise RuntimeError(
            f"[smallFactory] Error: default_datarepo not set in {CONFIG_FILENAME}. Run 'init' or set it manually."
        )
    return pathlib.Path(datarepo).expanduser().resolve()


def load_datarepo_config(repo_path: pathlib.Path | None = None) -> dict:
    """Read repository-level configuration from sfdatarepo.yml."""
    if repo_path is None:
        repo_path = get_datarepo_path()
    config_file = repo_path / DATAREPO_CONFIG_FILENAME
    if not config_file.exists():
        return {}
    with open(config_file) as f:
        return yaml.safe_load(f) or {}


def get_inventory_field_specs() -> dict:
    """Return the inventory field specs from sfdatarepo.yml, fallback to defaults.

    Structure:
      { field_name: { required: bool, regex: str, description: str } }
    """
    dr_cfg = load_datarepo_config()
    inv = dr_cfg.get("inventory", {})
    fields = inv.get("fields")
    if isinstance(fields, dict) and fields:
        return fields
    return INVENTORY_DEFAULT_FIELD_SPECS


# -------------------------------
# Entities field schema (YAML-driven)
# -------------------------------
# sfdatarepo.yml may define entity field specs under:
# entities:
#   fields: { <field>: {required, regex, description} }           # global defaults
#   types:
#     p: { fields: { ... } }     # per-type overrides/additions (by sfid prefix before '_')
#     l: { fields: { ... } }
# If types.<key> is a dict without a 'fields' key, treat it as a fields map directly.

def get_entities_specs(repo_path: pathlib.Path | None = None) -> dict:
    """Return entities spec blocks from sfdatarepo.yml.

    Shape: { 'fields': {..}, 'types': { type_key: {fields:{..}}|{..} } }
    Missing sections are returned as empty dicts.
    """
    dr_cfg = load_datarepo_config(repo_path)
    ent = dr_cfg.get("entities") or {}
    fields = ent.get("fields")
    types = ent.get("types")
    return {
        "fields": fields if isinstance(fields, dict) else {},
        "types": types if isinstance(types, dict) else {},
    }


def get_entity_field_specs_for_sfid(sfid: str, repo_path: pathlib.Path | None = None) -> dict:
    """Return merged field specs for a given entity sfid.

    Merges global entities.fields with per-type fields if present, where
    type key is derived from the prefix before the first underscore in sfid
    (e.g. 'p' for 'p_m3x10', 'l' for 'l_a1').
    """
    specs = get_entities_specs(repo_path)
    merged = dict(specs.get("fields", {}))
    type_prefix = sfid.split("_", 1)[0] if "_" in sfid else None
    if type_prefix:
        t = specs.get("types", {}).get(type_prefix) or specs.get("types", {}).get(f"{type_prefix}_")
        if isinstance(t, dict):
            # t may be {'fields': {...}} or the fields map itself
            tf = t.get("fields") if isinstance(t.get("fields"), dict) else None
            if tf is None:
                tf = t
            if isinstance(tf, dict):
                merged.update(tf)
    return merged


# -------------------------------
# Stickers configuration
# -------------------------------
def get_stickers_default_fields(repo_path: pathlib.Path | None = None) -> list[str]:
    """Return default fields for stickers batch page from sfdatarepo.yml.

    Expected structure in repo config:

      stickers:
        batch:
          default_fields: [manufacturer, value, size]

    Returns an empty list if not configured.
    """
    dr_cfg = load_datarepo_config(repo_path)
    stickers_cfg = dr_cfg.get("stickers")
    if not isinstance(stickers_cfg, dict):
        return []
    batch_cfg = stickers_cfg.get("batch")
    if not isinstance(batch_cfg, dict):
        return []
    df = batch_cfg.get("default_fields")
    if isinstance(df, (list, tuple)):
        # Normalize to list[str] with trimmed entries and drop empties
        out = []
        for x in df:
            try:
                s = str(x).strip()
            except Exception:
                continue
            if s:
                out.append(s)
        return out
    return []


# -------------------------------
# Vision / VLM configuration
# -------------------------------

def get_ollama_base_url() -> str:
    """Return the Ollama base URL from env, defaulting to localhost:11434.

    Env var: SF_OLLAMA_BASE_URL
    """
    return os.environ.get("SF_OLLAMA_BASE_URL", "http://localhost:11434")


def get_vision_model() -> str:
    """Return the vision model id to use with Ollama.

    Env var: SF_VISION_MODEL; default: qwen2.5vl:3b
    """
    return os.environ.get("SF_VISION_MODEL", "qwen2.5vl:3b")
