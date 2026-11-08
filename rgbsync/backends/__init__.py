"""Backend manager: discovers devices across OpenRGB-SDK and direct-HID."""
from __future__ import annotations

import logging
from typing import Dict, List

from ..device import SyncDevice
from .openrgb import OpenRGBClient


def _get_hid_backend():
    from .hid_generic import HidBackend  # lazy: hides optional import
    return HidBackend()


class Manager:
    def __init__(self, config):
        self.config = config
        self.openrgb: OpenRGBClient | None = None
        self._devices_sdk: List[SyncDevice] = []
        self.hid_devices: Dict[str, SyncDevice] = {}
        self.devices: List[SyncDevice] = []
        self.sdk_available = False

    def refresh(self) -> List[SyncDevice]:
        self._connect_sdk()
        self._discover_hid()
        self.devices = list(self._devices_sdk) + list(self.hid_devices.values())
        return self.devices

    # internal

    def _connect_sdk(self):
        host = self.config.get("sdk", {}).get("host", "127.0.0.1")
        port = self.config.get("sdk", {}).get("port", 6742)
        if self.openrgb is None:
            self.openrgb = OpenRGBClient(host, port)
        try:
            self._devices_sdk = self.openrgb.discover()
            self.sdk_available = True
            for d in self._devices_sdk:
                d.enabled = self.config.enabled(d.device_id)
        except (ConnectionError, OSError, ValueError) as exc:
            self.sdk_available = False
            self._devices_sdk = []
            if self.openrgb:
                self.openrgb.close()
                self.openrgb = None
            logging.getLogger(__name__).warning(
                "OpenRGB SDK (%s:%s) unreachable: %s", host, port, exc)

    def _discover_hid(self):
        self.hid_devices = {}
        try:
            for hid_dev in _get_hid_backend().discover():
                hid_dev.enabled = self.config.enabled(hid_dev.dev.device_id)
                self.hid_devices[hid_dev.dev.device_id] = hid_dev
        except Exception as exc:  # never let one bad profile kill the runner
            logging.getLogger(__name__).warning("hid backend error: %s", exc)

    def push_frame(self, device: SyncDevice, colors):
        if device.backend == "openrgb":
            if self.openrgb and self.sdk_available:
                self.openrgb.update_device(device.index, colors)
        else:  # hid
            hid_dev = self.hid_devices.get(device.device_id)
            if hid_dev:
                if not getattr(hid_dev, "handle", None):
                    try:
                        hid_dev.open()
                    except Exception as exc:
                        logging.getLogger(__name__).warning(
                            "hid open %s failed: %s", device.name, exc)
                        hid_dev.enabled = False
                        return
                hid_dev.update(colors)

    def shutdown(self):
        if self.openrgb:
            self.openrgb.close()
            self.openrgb = None
        for hid_dev in self.hid_devices.values():
            hid_dev.close()
