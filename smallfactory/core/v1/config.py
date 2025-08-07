import sys
import pathlib
import yaml

SF_TOOL_VERSION = "1.0"
CONFIG_FILENAME = ".smallfactory.yml"
DATAREPO_CONFIG_FILENAME = "sfdatarepo.yml"


def ensure_config() -> pathlib.Path:
    """Ensure local .smallfactory.yml exists; create with defaults if missing.

    Returns the path to the config file.
    """
    config_path = pathlib.Path(CONFIG_FILENAME)
    if not config_path.exists():
        config = {"default_datarepo": None}
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
        print(f"[smallfactory] Error: default_datarepo not set in {CONFIG_FILENAME}. Run 'create' or set it manually.")
        sys.exit(1)
    return pathlib.Path(datarepo).expanduser().resolve()
