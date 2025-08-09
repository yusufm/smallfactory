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
    # Core identifiers (required)
    "id": {"required": True, "regex": r"^[A-Za-z0-9._-]+$", "description": "Primary key for the inventory item."},
    "name": {"required": True, "regex": r"^.{1,200}$", "description": "Human-readable item name."},

    # Operational inputs (required when creating a record/location)
    "location": {"required": True, "regex": r"^[A-Za-z0-9 ._-]+$", "description": "Storage location name."},
    "quantity": {"required": True, "regex": r"^[0-9]+$", "description": "Quantity as non-negative integer (per location)."},

    # Common but optional metadata (regex allows empty)
    "description": {"required": False, "regex": r"^$|^.{1,500}$", "description": "Freeform description (<=500 chars)."},
    "category": {"required": False, "regex": r"^$|^.{1,500}$", "description": "Category or family."},
    "manufacturer": {"required": False, "regex": r"^$|^.{1,500}$", "description": "Manufacturer name."},
    "mpn": {"required": False, "regex": r"^[A-Za-z0-9 ._\-/#+]*$", "description": "Manufacturer Part Number."},
    "vendor": {"required": False, "regex": r"^$|^.{1,500}$", "description": "Preferred supplier/vendor."},
    "spn": {"required": False, "regex": r"^[A-Za-z0-9 ._\-/#+]*$", "description": "Supplier Part Number."},
    "notes": {"required": False, "regex": r"^$|^.{1,500}$", "description": "Additional notes."},
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
