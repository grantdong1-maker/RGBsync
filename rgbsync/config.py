"""Persistent runtime config (JSON) for the daemon + web UI."""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict

from . import DEFAULT_FPS, DEFAULT_SDK_HOST, DEFAULT_SDK_PORT

DEFAULTS: Dict[str, Any] = {
    "effect": "rainbow",
    "speed": 40.0,
    "brightness": 1.0,
    "fps": DEFAULT_FPS,
    "params": {},          # per-effect extra params (colors, etc.)
    "enabled_devices": {}, # device_id -> bool
    "sdk": {"host": DEFAULT_SDK_HOST, "port": DEFAULT_SDK_PORT},
}


def config_path() -> str:
    return os.environ.get(
        "RGBSYNC_CONFIG",
        os.path.join(os.path.expanduser("~"), ".config", "rgbsync", "config.json"))


class Config:
    def __init__(self, path: str | None = None):
        self.path = path or config_path()
        self.lock = threading.Lock()
        self.data = json.loads(json.dumps(DEFAULTS))  # deep copy
        self.load()

    # disk ----------
    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            for key, val in stored.items():
                if key in self.data and isinstance(self.data[key], dict) \
                        and isinstance(val, dict):
                    self.data[key].update(val)
                else:
                    self.data[key] = val
            if self.data["effect"] not in ("static", "rainbow", "wave",
                                           "breathing", "flash", "off"):
                self.data["effect"] = "rainbow"
        except (OSError, json.JSONDecodeError):
            pass  # first run / unreadable: fall back to defaults

    def save(self):
        with self.lock:
            path_dir = os.path.dirname(self.path) or "."
            os.makedirs(path_dir, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, indent=2)
            os.replace(tmp, self.path)

    # accessors ----------
    def get(self, key: str, default=None):
        return self.data.get(key, default)

    def set(self, key: str, value):
        self.data[key] = value

    def enabled(self, device_id: str, default: bool = True) -> bool:
        return bool(self.data["enabled_devices"].get(device_id, default))

    def set_enabled(self, device_id: str, value: bool):
        self.data["enabled_devices"][device_id] = bool(value)
