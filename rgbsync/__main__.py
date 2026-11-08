"""Command line entry: rgbsync [scan|serve|on|off|set|effects|demo]"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import DEFAULT_WEB_PORT
from .config import Config
from .daemon import Daemon
from .effects import REGISTRY


def cmd_scan(d: Daemon, args) -> int:
    devices = d.refresh()
    if not devices:
        print("no RGB devices found")
        print("hint: start the OpenRGB server once  ->  openrgb --server")
        return 1
    for dev in devices:
        marker = "" if dev.enabled else "  (muted)"
        print(f"- {dev.name}  [{dev.device_type}] via {dev.backend}"
              f" · {dev.led_count} LEDs{marker}")
        for m in dev.led_maps[:6]:
            print(f"    zone {m.zone}  x={m.x:.2f}")
    return 0


def cmd_serve(d: Daemon, args) -> int:
    from .web.server import serve_in_background
    d.refresh()
    if not d.manager.devices:
        print("no devices yet; effect engine will still run (poll-attach)")
    serve_in_background(d, args.port)
    print(f"dashboard → http://127.0.0.1:{args.port}")
    print("press Ctrl+C to stop; device lights restore to dark")
    try:
        while not d.stop_event.is_set():
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        d.stop()
    return 0


def cmd_set(d: Daemon, args) -> int:
    if args.key == "effect":
        d.set_effect(args.value)
    else:
        d.set_param(args.key, args.value)
    print(f"set {args.key} = {args.value}")
    return 0


def cmd_effects(d: Daemon, args) -> int:
    for name in REGISTRY:
        print(name)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="rgbsync",
        description="sync RGB effects across every connected device")
    p.add_argument("--sdk-host", default=None,
                   help="OpenRGB SDK server host (default 127.0.0.1)")
    p.add_argument("--demo", action="store_true",
                   help="run engine without touching actual devices")
    sub = p.add_subparsers(dest="cmd")

    scan = sub.add_parser("scan", help="list detected RGB devices")
    scan.set_defaults(func=cmd_scan)

    serve = sub.add_parser("serve", help="start daemon + web dashboard")
    serve.add_argument("--port", type=int, default=DEFAULT_WEB_PORT)
    serve.set_defaults(func=cmd_serve)

    setp = sub.add_parser("set", help="set effect/param combos")
    setp.add_argument("key", choices=["effect", "speed", "brightness", "fps"])
    setp.add_argument("value")
    setp.set_defaults(func=cmd_set)

    sub.add_parser("effects", help="list effects").set_defaults(func=cmd_effects)
    return p


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    if not hasattr(args, "func"):
        # no subcommand: let people know what this thing does
        build_parser().print_help()
        return 0
    config = Config()
    if args.sdk_host:
        config.data["sdk"]["host"] = args.sdk_host
    daemon = Daemon(config, demo=args.demo)
    return args.func(daemon, args)


if __name__ == "__main__":
    sys.exit(main())
