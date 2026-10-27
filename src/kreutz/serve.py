"""`kreutz serve`: the live dashboard over plain HTTP (standard library only).

  /            the dashboard page (web/index.html)
  /api/state   every camera's state.json, merged
  /<camera>/.. files the watcher wrote under data/live/ (images, state.json)
"""

from __future__ import annotations

import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import live, paths

WEB = Path(__file__).parent / "web"


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            return self._send(WEB / "index.html", "text/html; charset=utf-8")
        if route.startswith("/examples/") and route.endswith(".png") and "/.." not in route:
            p = WEB / "examples" / Path(route).name
            if p.exists():
                return self._send(p, "image/png")
        if route in ("/api/state", "/api/activity"):
            body = json.dumps(live.merged_state() if route == "/api/state" else live.merged_activity()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def end_headers(self):
        if self.path.endswith(".json"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _send(self, path: Path, ctype: str):
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        if not self.path.startswith("/api/"):
            super().log_message(fmt, *args)


def make_server(host: str, port: int, root: Path | None = None) -> ThreadingHTTPServer:
    root = root or paths.DATA / "live"
    root.mkdir(parents=True, exist_ok=True)
    return ThreadingHTTPServer((host, port), partial(Handler, directory=str(root)))


def serve(host: str = "0.0.0.0", port: int = 8080) -> None:
    srv = make_server(host, port)
    print(f"kreutz live on http://{host}:{port}/ serving {paths.DATA / 'live'}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
