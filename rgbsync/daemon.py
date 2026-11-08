"""Daemon core: find RGB devices, run one global effect across all of them.

The sync model is simple and effective: a single frame clock (`t`) drives
every device. Every frame, the effect paints a color for each LED of each
enabled device from the same `t`, then backends push their reports. That
is how every device stays visually in lock-step without any extra protocol.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Dict, List, Optional

from . import __version__, DEFAULT_FPS
from .backends import Manager
from .config import Config
from .device import SyncDevice
from .effects import REGISTRY, get_effect, time_now

log = logging.getLogger(__name__)


class Daemon:
    def __init__(self, config: Optional[Config] = None,
                 demo: bool = False, fps: int = DEFAULT_FPS):
        if config is None:
            config = Config()
        self.config = config
        self.manager = Manager(config)
        self.demo = demo  # demo = no pushes, just measure
        self.stop_event = threading.Event()
        self.frame_thread: Optional[threading.Thread] = None
        self.frames = 0
        self.errors_last_device: Dict[str, str] = {}
        if fps is not None and fps != config.get("fps"):
            config.set("fps", fps)

    # ---------- lifecycle ----------
    def refresh(self) -> List[SyncDevice]:
        devices = self.manager.refresh()
        for d in devices:
            d.enabled = self.config.enabled(d.device_id)
        return devices

    def start(self):
        self.refresh()
        self.frame_thread = threading.Thread(
            target=self._frame_loop, name="rgbsync-frames", daemon=True)
        self.frame_thread.start()

    def stop(self):
        self.stop_event.set()
        if self.frame_thread:
            self.frame_thread.join(timeout=5)
        self._paint_all_zero()          # leave nothing glowing behind
        self.manager.shutdown()

    # ---------- public controls (used by web UI + CLI) ----------
    def state(self) -> Dict:
        return {
            "version": __version__,
            "connected": self.manager.sdk_available,
            "effect": self.config.get("effect"),
            "speed": self.config.get("speed"),
            "brightness": self.config.get("brightness"),
            "fps": self.config.get("fps"),
            "params": self.config.get("params"),
            "frames": self.frames,
            "devices": [
                {"id": d.device_id, "name": d.name, "vendor": d.vendor,
                 "type": d.device_type, "backend": d.backend,
                 "location": d.location, "leds": d.led_count,
                 "enabled": self.config.enabled(d.device_id), "states":
                     self._device_state_summary(d)}
                for d in self.manager.devices
            ],
        }

    def set_effect(self, effect_name: str, params: Optional[Dict] = None):
        if effect_name not in REGISTRY:
            raise ValueError(f"unknown effect: {effect_name}")
        self.config.set("effect", effect_name)
        if params:
            merged = dict(self.config.get("params", {}))
            merged.update(params)
            self.config.set("params", merged)
        self.config.save()

    def set_param(self, key: str, value):
        allowed = ("speed", "brightness", "fps")
        if key not in allowed:
            raise ValueError(f"cannot set {key}")
        value = float(value)
        if key == "speed" and not (0 <= value <= 100):
            raise ValueError("speed is 0..100")
        if key == "brightness" and not (0.05 <= value <= 1.0):
            raise ValueError("brightness is 0.05..1.0")
        if key == "fps" and not (1 <= value <= 240):
            raise ValueError("fps is 1..240")
        self.config.set(key, value)
        self.config.save()

    def set_device_enabled(self, device_id: str, enabled: bool):
        self.config.set_enabled(device_id, enabled)
        self.config.save()
        for d in self.manager.devices:
            if d.device_id == device_id:
                d.enabled = enabled

    # ---------- frame engine ----------
    def _frame_loop(self):
        interval = 1.0 / max(1, int(self.config.get("fps", DEFAULT_FPS)))
        t0 = time_now()
        while not self.stop_event.is_set():
            t = time_now() - t0
            self._paint_frame(t)
            self.frames += 1
            # re-read fps in case the UI changed it
            interval = 1.0 / max(1, int(self.config.get("fps", DEFAULT_FPS)))
            self.stop_event.wait(interval)

    def _paint_frame(self, t: float):
        effect = get_effect(self.config.get("effect", "rainbow"))
        speed = float(self.config.get("speed", 40))
        brightness = float(self.config.get("brightness", 1.0))
        params = dict(effect.default_params)
        params.update(self.config.get("params", {}))
        for device in self.manager.devices:
            if not device.enabled or device.led_count <= 0:
                continue
            try:
                colors = effect.compute(device.led_maps, device.led_count,
                                        t, speed, brightness, params)
                if not self.demo:
                    self.manager.push_frame(device, colors)
            except Exception as exc:
                log.exception("frame failed for %s: %s", device.name, exc)
                self.errors_last_device[device.device_id] = str(exc)

    def _paint_all_zero(self):
        for device in self.manager.devices:
            try:
                if not self.demo:
                    self.manager.push_frame(device,
                                            [(0, 0, 0)] * device.led_count)
            except Exception:
                pass

    def _device_state_summary(self, d: SyncDevice) -> str:
        if d.backend == "openrgb" and not self.manager.sdk_available:
            return "openrgb server offline"
        if d.backend == "hid" and d.device_id not in self.manager.hid_devices:
            return "hid not opened"
        return "ready"
