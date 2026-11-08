# RGBSync — homemade RGB device sync (mouse, keyboard, etc.)

A homemade, single-binary-standalone Python daemon that drives **one synchronized
RGB effect across every RGB device it finds** — every device shares a frame
clock, so a rainbow sweeps across your mouse and keyboard simultaneously.

## How it works

```
 ┌ web dashboard / CLI ─┐
 │  pick effect, speed  │
 ▼                      │
 ┌ daemon (frame loop) ────────────────────────────┐
 │ effect engine: rainbow | wave | breathing | ... │
 │  one clock t, same t drives every device frame  │
 ▼            ▼                                    │
 OpenRGB SDK client   direct-HID (profile-driven)  │
 (talks to `openrgb --server`,                    │
  which drives mice/keyboards/…)                  │
```

- **OpenRGB SDK backend** — a clean-room reimplementation of the OpenRGB
  network wire protocol (`ORGB` packets, device listing decoding, per-LED
  updates). [`openrgb`](https://openrgb.org/) does the USB/kernel grunt work,
  giving broad device coverage without any kernel modules of our own.
- **Direct-HID backend** — entirely data-driven: drop a JSON profile in
  `rgbsync/profiles/` describing your device's HID reports, no code needed.

## Quick start

```bash
# 1) get a device transport running (one-time)
sudo apt install openrgb          # or your distro package / follow openrgb.org
sudo openrgb --server             # it probes devices, expose SDK at port 6742

# 2) run rgb sync
cd rgbsync
python3 -m rgbsync scan           # lists synced devices
python3 -m rgbsync serve          # daemon + web dashboard on :8899
```

Then open the dashboard: **http://127.0.0.1:8899**

Remote/lighter setups also work (daemon only, no web UI):

```bash
rgbsync set effect rainbow
rgbsync set speed 55
rgbsync set effect static             # static uses params.color from the dashboard
rgbsync set brightness 0.8
rgbsync serve --port 8899
```

## Dashboard

- Pick effects, tune speed / brightness / fps live.
- Per-color pickers for gradient effects.
- Click any device name to mute/unmute it (e.g. mute only the mouse).
- Global "frames pushed" counter — a quick way to sanity-check the loop is live.

## Effects

| name        | params (pick colors in UI)          |
|-------------|-------------------------------------|
| static      | color                               |
| rainbow     | –                                   |
| wave        | color, color2                       |
| breathing   | color                               |
| flash       | color                               |
| off         | –                                   |

Config saved at `~/.config/rgbsync/config.json` (override with `RGBSYNC_CONFIG=`).

## Adding your own protocol (direct HID)

Everything about a device's raw protocol lives in a profile JSON
(see `rgbsync/profiles/example.json`):

```json
{
  "name": "My keyboard",
  "match": {"vendor_id": 1234, "product_id": 5678},
  "matrix": {"rows": 6, "cols": 22},
  "report": {
    "id": 4, "size": 64,
    "init":  [{"bytes": [0,3,1]}],
    "data":  [3, "{i}", "{r}", "{g}", "{b}", 0]
  }
}
```

Tokens: `{i}` LED index, `{r}`/`{g}`/`{b}` color bytes, `{col}` column.
Nothing is written unless the daemon finds a matching VID/PID *and* the
dashboard has that device enabled. Also grab hidapi support:

```bash
pip3 install hidapi
# plus udev rule so your user may talk to hidraw devices
sudo cp udev/99-rgbsync.rules /etc/udev/rules.d/
```

## Safety / design notes

- Direct-HID writes are **opt-in**: an unmuted device that errored is dropped,
  logged, and only retried on a manual rescan.
- The OpenRGB SDK backend never pokes your kernel/USB hardware directly; it
  relies on the (already battle-tested) `openrgb` server process.
- On-stop dark-paint: when the daemon exits, every device is set to black
  rather than freezing on the last frame.

## File layout

```
rgbsync/
├── rgbsync/
│   ├── __main__.py            # CLI
│   ├── daemon.py              # device sync engine + shared effect clock
│   ├── config.py              # persistent state
│   ├── device.py              # data model + compact LE protocol helpers
│   ├── effects/               # effect engine (from-scratch math)
│   ├── backends/
│   │   ├── openrgb.py         # OpenRGB SDK wire-protocol client
│   │   └── hid_generic.py     # template driven direct HID writes
│   └── web/
│       ├── server.py          # stdlib-only http server + JSON API
│       └── static/index.html  # dashboard (no JS deps)
├── requirements.txt
└── README.md
```
