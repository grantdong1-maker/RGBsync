"""Effect engine: each effect paints one frame of RGB colors.

Color math is copyright-free original code; the effects follow common
generic ideas (rainbow gradient, sine wave, etc.) implemented from scratch.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Tuple

try:
    import colorsys
except ImportError:  # pragma: no cover
    colorsys = None

RGB = Tuple[int, int, int]


class Effect:
    name = "static"
    label = "Static"
    default_params: Dict = {"color": "#FF0000"}

    def compute(
        self,
        led_maps,
        led_count: int,
        t: float,
        speed: float,
        brightness: float,
        params: Dict,
    ) -> List[RGB]:
        raise NotImplementedError


def hsv(h: float, s: float, v: float) -> RGB:
    if colorsys is None:
        # minimal HSV->RGB as fallback (h in [0,1])
        i = math.floor(h * 6) % 6
        f = h * 6 - math.floor(h * 6)
        p = 1 - s
        q = 1 - f * s
        t_ = 1 - (1 - f) * s
        seg = [(v, t_, p), (q, v, p), (p, v, t_), (p, q, v), (t_, p, v), (v, p, q)]
        r, g, b = seg[i]
        return (int(r * 255), int(g * 255), int(b * 255))
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, v)
    return (int(r * 255), int(g * 255), int(b * 255))


def hex_to_rgb(value: str, fallback: RGB = (255, 255, 255)) -> RGB:
    value = (value or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore
    except ValueError:
        return fallback


def scale(c: RGB, brightness: float) -> RGB:
    return (max(0, min(255, int(c[0] * brightness))),
            max(0, min(255, int(c[1] * brightness))),
            max(0, min(255, int(c[2] * brightness))))


class Static(Effect):
    name = "static"
    label = "Static color"

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        c = scale(hex_to_rgb(params.get("color", "#FF0000")), brightness)
        return [c] * led_count


class Off(Effect):
    name = "off"
    label = "Off"
    default_params = {}

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        return [(0, 0, 0)] * led_count


class Rainbow(Effect):
    name = "rainbow"
    label = "Rainbow cycle"
    default_params = {}

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        phase = t * (speed / 20.0)
        base = []
        for m in led_maps:
            x = m.x if led_maps else 0.0
            h_pos = (phase + x * 0.45) % 1.0
            base.append(scale(hsv(h_pos, 1.0, 1.0), brightness))
        return base


class Wave(Effect):
    name = "wave"
    label = "Wave"
    default_params = {"color": "#00A0FF", "color2": "#70008A"}

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        c1 = hex_to_rgb(params.get("color", "#00A0FF"))
        c2 = hex_to_rgb(params.get("color2", "#70008A"))
        out = []
        for m in led_maps:
            x = m.x if led_maps else 0.0
            wave_pos = (x * math.pi * 1.5 - t * speed) % (math.pi * 2)
            f = (math.sin(wave_pos) + 1) / 2
            out.append(scale(_lerp(c1, c2, f), brightness))
        return out


class Breathing(Effect):
    name = "breathing"
    label = "Breathing"
    default_params = {"color": "#00FFFF"}

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        c = hex_to_rgb(params.get("color", "#00FFFF"))
        pulse = (math.sin(t * (speed / 30.0)) + 1) / 2
        pulse = max(0.05, pulse)
        return [scale(c, brightness * pulse)] * led_count


class Flash(Effect):
    name = "flash"
    label = "Flash alert"
    default_params = {"color": "#FF0000"}

    def compute(self, led_maps, led_count, t, speed, brightness, params):
        c = hex_to_rgb(params.get("color", "#FF0000"))
        period = max(0.1, 60.0 / max(1.0, speed))
        on = (t % period) < (period / 2)
        return [scale(c, brightness if on else 0.0)] * led_count


def _lerp(a: RGB, b: RGB, f: float) -> RGB:
    return tuple(int(a[i] + (b[i] - a[i]) * f) for i in range(3))  # type: ignore


REGISTRY = {e.name: e for e in (Static(), Rainbow(), Wave(), Breathing(), Flash(), Off())}


def get_effect(name: str) -> Effect:
    return REGISTRY.get(name, REGISTRY["static"])


def effect_names() -> List[dict]:
    return [{"name": e.name, "label": e.label, "params": e.default_params}
            for e in REGISTRY.values()]


def time_now() -> float:
    return time.monotonic()
