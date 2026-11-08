"""Device model + little-endian binary helpers shared by backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

DEVICE_TYPES = {
    0: "Motherboard",
    1: "Keyboard",
    2: "Mouse",
    3: "Mousemat",
    4: "Headset",
    5: "Headset Stand",
    6: "Gamepad",
    7: "Light",
    8: "Speaker",
    9: "Memory Module",
    10: "Monitor",
    11: "Fan Controller",
    12: "GPU",
    13: "Strip",
    14: "Storage",
    15: "Case",
    16: "Microphone",
    17: "Accessory",
}


def u16(value: int) -> bytes:
    return value.to_bytes(2, "little")


def u32(value: int) -> bytes:
    return value.to_bytes(4, "little")


class BufReader:
    """Sequential reader for the little-endian OpenRGB protocol layout."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def u8(self) -> int:
        value = self.data[self.pos]
        self.pos += 1
        return value

    def u16(self) -> int:
        value = int.from_bytes(self.data[self.pos:self.pos + 2], "little")
        self.pos += 2
        return value

    def u32(self) -> int:
        value = int.from_bytes(self.data[self.pos:self.pos + 4], "little")
        self.pos += 4
        return value

    def string(self) -> str:
        length = self.u32()
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        return raw.decode("utf-8", errors="replace")

    def remaining(self) -> int:
        return len(self.data) - self.pos


@dataclass
class LedMap:
    """Where one LED sits in the visual layout, used by effects."""
    led_index: int
    zone: str
    x: float  # normalized 0..1 horizontal position in the whole device
    y: float  # normalized 0..1 vertical position


@dataclass
class SyncDevice:
    backend: str                 # "openrgb" or "hid"
    device_id: str               # stable-ish unique id within this daemon
    index: int                   # device index on its backend
    name: str
    vendor: str
    device_type: str
    led_count: int
    location: str = ""
    led_maps: List[LedMap] = field(default_factory=list)
    enabled: bool = True

    def maps_by_index(self) -> List[LedMap]:
        return self.led_maps
