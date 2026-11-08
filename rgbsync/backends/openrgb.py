"""OpenRGB SDK wire protocol client (protocol v1 parse, v0-6 aware).

Wire format (verified against an OpenRGB 0.9 server):

  packet = "ORGB" | dev_id:u32le | pkt_id:u32le | pkt_size:u32le | data

Client flow:
  1. SET_CLIENT_NAME (50)              - announce "rgbsync"
  2. REQUEST_PROTOCOL_VERSION (40)     - data u32(client_max), reply u32(server)
  3. REQUEST_CONTROLLER_COUNT (0)      - reply u32 count (+id list at proto 6)
  4. REQUEST_CONTROLLER_DATA (1)       - per device; data u32(requested level)
  5. UPDATE_LEDS (1050)                - per frame per device

Device data blocks are parsed at protocol level 1 (the lowest level that
includes the vendor string), which keeps the format stable across servers:
    u32 size | i32 type | name | vendor | description | fw_version |
    serial | location | u16 n_modes | i32 active_mode | modes... |
    u16 n_zones | zones... | u16 n_leds | leds... | u16 n_colors | colors
Strings are u16-length prefixed (length includes the null terminator).
Colors are 4 bytes: R, G, B, then an ignored byte.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
from typing import List, Optional, Tuple

from ..device import BufReader, LedMap, SyncDevice

log = logging.getLogger(__name__)

MAGIC = b"ORGB"

# packet ids
CMD_REQUEST_CONTROLLER_COUNT = 0
CMD_REQUEST_CONTROLLER_DATA = 1
CMD_REQUEST_PROTOCOL_VERSION = 40
CMD_SET_CLIENT_NAME = 50
CMD_DEVICE_LIST_UPDATED = 100
CMD_REQUEST_RESCAN_DEVICES = 140
CMD_UPDATE_LEDS = 1050
CMD_SET_CUSTOM_MODE = 1100

OPENRGB_MAX_PACKET = 65535
CLIENT_NAME = b"rgbsync\x00"
PARSE_LEVEL = 1  # controller data requested at this protocol version

DEVICE_TYPES = {
    0: "Motherboard", 1: "DRAM", 2: "GPU", 3: "Cooler", 4: "LEDStrip",
    5: "Keyboard", 6: "Mouse", 7: "Mousemat", 8: "Headset",
    9: "Headset Stand", 10: "Gamepad", 11: "Light", 12: "Speaker",
    13: "Virtual", 14: "Storage", 15: "Case", 16: "Microphone",
    17: "Accessory", 18: "Keypad", 19: "Unknown",
}


def make_packet(dev_id: int, pkt_id: int, data: bytes = b"") -> bytes:
    return MAGIC + struct.pack("<III", dev_id, pkt_id, len(data)) + data


class OpenRGBClient:
    """One TCP connection to an OpenRGB SDK server (USB transport layer)."""

    def __init__(self, host: str, port: int, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: Optional[socket.socket] = None
        self.negotiated_version = 1
        self.controller_ids: List[int] = []
        self.lock = threading.Lock()
        self._direct_mode_told: set[int] = set()

    # ---------------- connection ----------------
    def connect(self):
        if self.sock:
            return
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)
        self._send(255, CMD_SET_CLIENT_NAME, CLIENT_NAME)
        self._send(255, CMD_REQUEST_PROTOCOL_VERSION, struct.pack("<I", 5))
        try:
            body = self._recv_packet_body()
            server_version = struct.unpack("<I", body)[0]
        except (socket.timeout, ValueError):
            server_version = 0  # very old server: no version negotiation
            log.info("OpenRGB server did not negotiate; assuming protocol 0")
        self.negotiated_version = min(5, server_version) if server_version else 0
        log.info("OpenRGB SDK negotiated protocol v%s (server max v%s)",
                 self.negotiated_version, server_version)

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except OSError:
            pass
        self.sock = None
        self._direct_mode_told.clear()

    # ---------------- packet level ----------------
    def _send(self, dev_id: int, pkt_id: int, data: bytes = b""):
        assert self.sock is not None
        self.sock.sendall(make_packet(dev_id, pkt_id, data))

    def _recv_exact(self, size: int) -> bytes:
        chunks = []
        remaining = size
        while remaining > 0:
            chunk = self.sock.recv(remaining)  # type: ignore
            if not chunk:
                raise ConnectionError("OpenRGB server closed the connection")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _recv_packet_body(self) -> bytes:
        header = self._recv_exact(16)
        if header[:4] != MAGIC:
            raise ValueError(f"bad magic from OpenRGB server: {header[:4]!r}")
        _, _dev_id, pkt_size = struct.unpack("<III", header[4:16])
        return self._recv_exact(pkt_size) if pkt_size else b""

    # ---------------- discovery ----------------
    def rescan(self):
        """Ask the server to redetect hardware (protocol 5+, no-op below 5)."""
        if self.negotiated_version < 5:
            return
        with self.lock:
            self.connect()
            self._send(255, CMD_REQUEST_RESCAN_DEVICES)
        # the server redetects asynchronously; give it a moment
        import time
        time.sleep(3)

    def discover(self) -> List[SyncDevice]:
        with self.lock:
            return self._discover()

    def _discover(self) -> List[SyncDevice]:
        self.connect()
        self._send(255, CMD_REQUEST_CONTROLLER_COUNT)
        body = self._recv_packet_body()
        (count,) = struct.unpack_from("<I", body, 0)
        if self.negotiated_version >= 6 and len(body) >= 4 + 4 * count:
            self.controller_ids = list(
                struct.unpack_from(f"<{count}I", body, 4))
        else:
            self.controller_ids = list(range(count))

        devices: List[SyncDevice] = []
        for idx, dev_id in enumerate(self.controller_ids):
            data_arg = struct.pack("<I", PARSE_LEVEL) \
                if self.negotiated_version >= 1 else b""
            self._send(dev_id, CMD_REQUEST_CONTROLLER_DATA, data_arg)
            try:
                body = self._recv_packet_body()
                dev = self._parse_controller(idx, dev_id, body)
            except Exception as exc:
                log.warning("device %s: parse degraded (%s)", idx, exc)
                dev = SyncDevice(backend="openrgb",
                                 device_id=f"openrgb:{dev_id}",
                                 index=dev_id, name=f"OpenRGB device {idx}",
                                 vendor="", device_type="Unknown", led_count=0)
            devices.append(dev)
        return devices

    # ---------------- device data parse (level 1) ----------------
    def _parse_controller(self, idx: int, dev_id: int, body: bytes) -> SyncDevice:
        r = BufReader(body)
        _dsize = r.u32()                       # size prefix of the block
        dtype = r.u32()
        name = r.string()
        if self.negotiated_version >= 1:
            vendor = r.string()
        else:
            vendor = ""
        r.string()                             # description
        r.string()                             # firmware version
        r.string()                             # serial
        r.string()                             # location

        # modes (consume; we only subscribe to direct per-LED updates)
        nmodes = r.u16()
        _active_mode = r.u32()
        for _ in range(min(nmodes, 512)):
            _mname = r.string()
            _value = r.u32()
            _flags = r.u32()
            r.u32()                            # speed_min
            r.u32()                            # speed_max
            if self.negotiated_version >= 3:
                r.u32()                        # brightness_min
                r.u32()                        # brightness_max
            r.u32()                            # colors_min
            r.u32()                            # colors_max
            r.u32()                            # speed
            if self.negotiated_version >= 3:
                r.u32()                        # brightness
            r.u32()                            # direction
            color_mode = r.u32()
            if color_mode in (1, 2, 3):        # per-led / zone / specific
                ncolors = r.u16()
                r.pos += ncolors * 4

        zones: List[dict] = []
        nzones = r.u16()
        for _ in range(min(nzones, 256)):
            zone = {"name": r.string(), "type": r.u32(),
                    "leds_min": r.u32(), "leds_max": r.u32(),
                    "led_count": r.u32()}
            (msize,) = struct.unpack_from("<H", r.data, r.pos)
            r.pos += 2
            if msize > 0:
                height = r.u32()
                width = r.u32()
                zone["matrix"] = (height, width)
                zone["map"] = [r.u32() for _ in range(height * width)]
            for _ in range(min(r.u16() if self.negotiated_version >= 4 else 0,
                               128)):
                r.string()
                r.u32()          # segment type
                r.u32()          # start_idx
                r.u32()          # leds_count
                if self.negotiated_version >= 6:
                    (smsize,) = struct.unpack_from("<H", r.data, r.pos)
                    r.pos += 2
                    r.pos += smsize
                    r.u32()      # segment flags
            if self.negotiated_version >= 5:
                r.u32()          # zone flags
            if self.negotiated_version >= 6:
                r.u32()          # zone active mode
                nz_modes = r.u16()
                for _ in range(nz_modes):
                    r.string()
                    r.u32(); r.u32()
                    r.u32(); r.u32()
                    r.u32(); r.u32()
                    r.u32(); r.u32()
                    r.u32()
                    cm = r.u32()
                    if cm in (1, 2, 3):
                        n = r.u16()
                        r.pos += n * 4
            zones.append(zone)

        nleds = r.u16()
        for _ in range(min(nleds, 4096)):
            r.string()                          # led name
            if self.negotiated_version < 6:
                r.u32()                         # led value

        dev = SyncDevice(backend="openrgb", device_id=f"openrgb:{dev_id}",
                         index=dev_id, name=name or f"Device {idx}",
                         vendor=vendor,
                         device_type=DEVICE_TYPES.get(dtype, str(dtype)),
                         led_count=max(1, nleds))
        dev.led_maps = self._build_led_maps(zones, nleds)
        return dev

    @staticmethod
    def _build_led_maps(zones: List[dict], led_count: int) -> List[LedMap]:
        """Normalized (x, y) spot for every LED, for gradient effects."""
        maps: List[LedMap] = [LedMap(i, "leds", i / max(1, led_count - 1), 0.5)
                              for i in range(led_count)]
        base = 0
        for z in zones:
            count = max(1, z["led_count"])
            if z.get("matrix"):
                height, width = z["matrix"]
                for row in range(height):
                    for col in range(width):
                        if row * width + col >= len(z["map"]):
                            break
                        led_idx = z["map"][row * width + col]
                        if 0 <= led_idx < led_count:
                            maps[led_idx] = LedMap(
                                led_idx, z["name"],
                                col / max(1, width - 1),
                                row / max(1, height - 1))
            else:
                for i in range(count):
                    if base + i < led_count:
                        maps[base + i] = LedMap(
                            base + i, z["name"],
                            (base + i) / max(1, led_count - 1), 0.5)
            base += count
        return maps

    # ---------------- streaming ----------------
    def update_device(self, dev_id: int, colors: List[Tuple[int, int, int]]):
        """Push one frame of RGB (list of (r,g,b)) to one device."""
        payload = bytearray(struct.pack("<H", len(colors)))
        for (red, green, blue) in colors:
            payload += bytes((red, green, blue, 0))
        data = struct.pack("<I", len(payload)) + bytes(payload)
        with self.lock:
            self.connect()
            if dev_id not in self._direct_mode_told:
                # switch the controller into direct (custom) mode so the
                # firmware's own pattern does not compete with our frames
                self._send(dev_id, CMD_SET_CUSTOM_MODE)
                self._direct_mode_told.add(dev_id)
            self._send(dev_id, CMD_UPDATE_LEDS, data)
