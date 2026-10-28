"""`kreutz serve`: the live dashboard over plain HTTP (standard library only).

  /            the dashboard page (web/index.html)
  /api/state   every camera's state.json, merged
  /api/activity what each watcher is doing now
  /api/movie   ?camera=&id= builds (once per growth) the hi-res movie of a track
  /api/label   POST {camera, id, label}
  /api/export  POST {camera, id}: writes the hunter's package to results/<camera>/live-finds/<id>/,
               pushes it, and returns the GitHub link
  /finds/<camera>/<id>/<file>  that package, served locally
  /<camera>/.. files the watcher wrote under data/live/ (images, state.json)
"""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import export, live, movie, paths

WEB = Path(__file__).parent / "web"
MOVIE_LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            return self._send(WEB / "index.html", "text/html; charset=utf-8")
        if route == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        if route.startswith("/examples/") and route.endswith(".png") and "/.." not in route:
            p = WEB / "examples" / Path(route).name
            if p.exists():
                return self._send(p, "image/png")
        if route.startswith("/finds/") and "/.." not in route:
            p = export.finds_dir(route.split("/")[2]) / "/".join(route.split("/")[3:]) if route.count("/") >= 4 else None
            if p and p.is_file():
                ctype = {"md": "text/markdown; charset=utf-8", "png": "image/png", "gif": "image/gif"}.get(p.suffix[1:], "application/octet-stream")
                return self._send(p, ctype)
            self.send_error(404)
            return
        if route == "/api/movie":
            return self._movie(parse_qs(self.path.partition("?")[2]))
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

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route not in ("/api/label", "/api/export"):
            self.send_error(404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if route == "/api/label":
                rec = live.record_label(body["camera"], body["id"], body["label"])
            else:
                cam, d = self._track(body["camera"], body["id"])
                if d is None:
                    self.send_error(404, "no such track")
                    return
                with MOVIE_LOCK:
                    rec = export.export_track(body["camera"], d, cam.get("latest"), Path(self.directory))
        except (KeyError, ValueError, json.JSONDecodeError) as e:
            self.send_error(400, str(e))
            return
        out = json.dumps(rec).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    @staticmethod
    def _track(camera: str, tid: str):
        """(camera state, track summary) from the merged state, or (None, None)."""
        for s in live.merged_state()["cameras"]:
            if s["camera"] == camera:
                return s, next((t for t in s["tracks"] if t["id"] == tid), None)
        return None, None

    def _movie(self, q: dict):
        """Build (once per track growth) and describe the hi-res movie of one track."""
        camera, tid = q.get("camera", [""])[0], q.get("id", [""])[0]
        stack = int(q.get("stack", [movie.STACK])[0])
        if stack not in (3, movie.STACK):
            self.send_error(400, "stack must be 3 or 8")
            return
        _, d = self._track(camera, tid)
        if d is None:
            self.send_error(404, "no such track")
            return
        try:
            with MOVIE_LOCK:
                meta = movie.movie(camera, d, Path(self.directory) / camera / "movies", stack)
        except Exception as e:  # missing frames, unreadable maps: tell the page, keep serving
            self.send_error(500, str(e))
            return
        body = json.dumps(meta).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

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
