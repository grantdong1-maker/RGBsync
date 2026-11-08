"""Direct-HID backend, driven entirely by JSON profile files.

No vendor protocol knowledge is baked into the code: a profile declares
which report(s) to write and how to slot colors/LED indices into it.

Each frame the backend renders one report per LED (or per row of a key
matrix) by filling the `data` template. Template tokens:

    {i}   zero-based LED index            {r} {g} {b}  color parts
    {row} row index                       {col} column index (row split)

Only integers 0-255 may result. Writing garbage to random HID devices is
how bricks get made; that is why nothing here ships auto-enabled.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from ..device import LedMap, SyncDevice

log = logging.getLogger(__name__)

try:
    import hid  # type: ignore  (python3-hidapi / pip package "hidapi")
    HAVE_HID = True
except ImportError as exc:
    HAVE_HID = False
    hid_err = exc
else:
    hid_err = None

PROFILES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                            "profiles")


def render_row(template: List[Any], led_index: int, col: int, rgb: Tuple[int, int, int]) -> bytes:
    """Fill one report row: plain ints pass through, tokens are substituted."""
    out = bytearray()
    for token in template:
        if not isinstance(token, str):
            out.append(token & 0xFF)
            continue
        out.append({
            "{i}": led_index & 0xFF,
            "{col}": col & 0xFF,
            "{r}": rgb[0] & 0xFF,
            "{g}": rgb[1] & 0xFF,
            "{b}": rgb[2] & 0xFF,
        }.get(token.lower(), 0))
    return bytes(out)


class HidProfileDevice:
    def __init__(self, profile: Dict, path_bytes: bytes):
        self.profile = profile
        self.path = path_bytes
        match = profile["match"]
        self.vid = match["vendor_id"]
        self.pid = match["product_id"]
        name = profile.get("name", f"{self.vid:04x}:{self.pid:04x}")
        rows = int(profile.get("matrix", {}).get("rows", 1))
        cols = int(profile.get("matrix", {}).get("cols", 1))
        self.rows, self.cols = rows, cols
        led_count = rows * cols
        loc = profile.get("location", "HID")
        self.dev = SyncDevice(backend="hid", device_id=f"hid:{self.vid:04x}:{self.pid:04x}",
                              index=0, name=name, vendor=match.get("vendor", ""),
                              device_type=profile.get("type", "Peripheral"),
                              led_count=led_count, location=loc)
        self.dev.led_maps = [
            LedMap(i, "matrix", 0 if cols <= 1 else (i % cols) / (cols - 1),
                   0.5 if rows <= 1 else (i // cols) / (rows - 1))
            for i in range(led_count)]
        self.handle = None

    # ---- lifecycle ----
    def open(self):
        dev = hid.device()
        dev.open_path(self.path)
        dev.set_nonblocking(False)
        self.handle = dev

    def close(self):
        try:
            if self.handle:
                self.handle.close()
        except Exception:
            pass
        self.handle = None

    # ---- rendering ----
    def update(self, colors: List[Tuple[int, int, int]]):
        dev = self.handle
        if not dev:
            return
        report = self.profile["report"]
        rid = int(report.get("id", 0))
        payload_pad = int(report.get("size", 0))
        # 1) whole-device level init / effect-mode bytes (unowned "header")
        for step in report.get("init", []):
            raw = render_row(step["bytes"], 0, 0, (0, 0, 0))
            _write(dev, raw, rid, payload_pad)
        # 2) per-LED / per-cell frames
        data_tpl = report.get("data")
        if data_tpl is None:
            return
        for i, rgb in enumerate(colors):
            col = i % max(1, self.cols)
            raw = render_row(data_tpl, i, col, rgb)
            _write(dev, raw, rid, payload_pad)


def _write(dev, raw: bytes, rid: int, size: int):
    # feature reports need the report id as first byte in the buffer
    buf = (bytes([rid]) + raw).ljust(size + 1 if rid else size, b"\0")
    try:
        if rid:
            dev.send_feature_report(buf)
        else:
            dev.write(buf)
    except OSError as exc:
        log.error("HID write failed: %s", exc)


class HidBackend:
    def __init__(self, profile_paths: Optional[List[str]] = None):
        self.paths = profile_paths

    def _profiles(self) -> List[Dict]:
        files = self.paths
        if not files:
            files = [os.path.join(PROFILES_DIR, fn)
                     for fn in sorted(os.listdir(PROFILES_DIR))
                     if fn.endswith(".json")]
        loaded = []
        for path in files:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    loaded.append(json.load(fh))
            except (OSError, json.JSONDecodeError, KeyError) as exc:
                log.warning("hid profile %s skipped: %s", path, exc)
        return loaded

    def discover(self) -> List[HidProfileDevice]:
        if not HAVE_HID:
            log.warning("hidapi not available (%s): direct profiles disabled", hid_err)
            return []
        out: List[HidProfileDevice] = []
        for prof in self._profiles():
            match = prof.get("match", {})
            target_vid = match.get("vendor_id", 0)
            target_pid = match.get("product_id", 0)
            for info in hid.enumerate():
                if (info["vendor_id"] == target_vid
                        and info["product_id"] == target_pid
                        and (target_pid != 0 or target_vid != 0)):
                    try:
                        out.append(HidProfileDevice(prof, info["path"]))
                    except Exception as exc:
                        log.warning("hid profile device open failed: %s", exc)
        return out
