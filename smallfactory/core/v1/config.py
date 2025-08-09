import sys
import pathlib
import yaml

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


def ensure_config() -> pathlib.Path:
    """Ensure local .smallfactory.yml exists; create with defaults if missing.

    Returns the path to the config file.
    """
    config_path = pathlib.Path(CONFIG_FILENAME)
    if not config_path.exists():
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
    config_path = pathlib.Path(CONFIG_FILENAME)
    with open(config_path, "w") as f:
        yaml.safe_dump(config, f)


def get_datarepo_path() -> pathlib.Path:
    config = load_config()
    datarepo = config.get("default_datarepo")
    if not datarepo:
        print(f"[smallfactory] Error: default_datarepo not set in {CONFIG_FILENAME}. Run 'init' or set it manually.")
        sys.exit(1)
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
