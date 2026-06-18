"""Tiny persistent config at ~/.config/youtube-tldw/config.json.

Currently just the default output ("render") folder, but generic for future keys.
"""

from __future__ import annotations

import json
from pathlib import Path

CONFIG_FILE = Path.home() / ".config" / "youtube-tldw" / "config.json"


def load() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text())
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def set_value(key: str, value) -> None:
    cfg = load()
    cfg[key] = value
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")


def unset(key: str) -> bool:
    cfg = load()
    if key not in cfg:
        return False
    del cfg[key]
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2) + "\n")
    return True
