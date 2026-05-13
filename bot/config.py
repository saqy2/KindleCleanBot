import os
import threading
from pathlib import Path

import yaml

_CONFIG = None
_LOCK = threading.Lock()
_BASE_DIR = Path(__file__).resolve().parent.parent


def load_config() -> dict:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    with _LOCK:
        if _CONFIG is not None:
            return _CONFIG

        config_path = os.environ.get("NOVEL_BOT_CONFIG", str(_BASE_DIR / "config.yaml"))

        with open(config_path, "r", encoding="utf-8") as f:
            _CONFIG = yaml.safe_load(f)

        # Environment variable overrides
        env_map = {
            "NOVEL_BOT_TOKEN": ("telegram", "token"),
            "NOVEL_DEEPSEEK_KEY": ("deepseek", "api_key"),
            "NOVEL_DEEPSEEK_MODEL": ("deepseek", "model"),
            "NOVEL_DEEPSEEK_BASE_URL": ("deepseek", "base_url"),
            "NOVEL_KAF_CLI": ("kaf_cli", "path"),
        }

        for env_var, (section, key) in env_map.items():
            val = os.environ.get(env_var)
            if val:
                _CONFIG.setdefault(section, {})[key] = val

        return _CONFIG


def get_config() -> dict:
    if _CONFIG is None:
        load_config()
    return _CONFIG
