"""Web UI + JSON API, stdlib-only, so there is nothing else to install."""

from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

log = logging.getLogger(__name__)

HERE = os.path.dirname(__file__)
STATIC = os.path.join(STATIC_DIR := os.path.join(HERE, "static"), "index.html")


def _json(handler, code: int, payload):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def make_server(daemon, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence default request spam
            log.debug(fmt, *args)

        def _read_body(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            if length <= 0:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError:
                return {}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                _json(self, 200, daemon.state())
            elif path == "/api/effects":
                from ..effects import effect_names
                _json(self, 200, effect_names())
            elif path in ("/", "/index.html"):
                try:
                    with open(STATIC, "rb") as fh:
                        body = fh.read()
                except OSError:
                    body = b"<h1>rgbsync</h1><p>dashboard missing</p>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._read_body()
            try:
                if path == "/api/effect":
                    daemon.set_effect(body.get("effect", "rainbow"),
                                      body.get("params"))
                elif path == "/api/param":
                    daemon.set_param(body["key"], body["value"])
                elif path == "/api/device":
                    daemon.set_device_enabled(body["id"], bool(body.get("enabled")))
                elif path == "/api/refresh":
                    daemon.refresh()
                    _json(self, 200, daemon.state())
                    return
                elif path == "/api/shutdown":
                    daemon.stop_event.set()
                    _json(self, 200, {"ok": True})
                    return
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                _json(self, 200, daemon.state())
            except (KeyError, ValueError) as exc:
                _json(self, 400, {"error": str(exc)})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve_in_background(daemon, port: int) -> ThreadingHTTPServer:
    """Start HTTP server on a thread for the caller (used by `rgbsync serve`)."""
    httpd = make_server(daemon, port)
    threading.Thread(target=httpd.serve_forever, name="rgbsync-web",
                     daemon=True).start()
    log.info("dashboard: http://127.0.0.1:%s", port)
    return httpd
