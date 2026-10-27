"""Hi-res movie of one track, built on demand for the dashboard's detail view.

Unlike the contact sheet (which follows the track, so the object sits still and
the background slides), the movie is a fixed patch of sky: the object crosses it.
Each frame is the stage-A significance map (stars and corona already modelled
out) with the previous STACK-1 maps shifted along the fitted motion and added
(noise / sqrt STACK, a real object adds up). Pixels are stored on a linear
0..RANGE_SIGMA scale; the page chooses the black point, so the background can
be pushed to pure black without rebuilding anything.

Output: a sprite sheet PNG (frames in a grid, row-major) and a JSON sidecar
with the frame names, grid layout and the fitted position per frame.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import coords
from .detect import frames as A
from .detect.velocity import FRAME_SECONDS
from .frameindex import FrameIndex
from .inspect import write_png

ZOOM = 4             # screen px per half-res map px (= 2x the camera's full resolution)
MARGIN = 20          # half-res px of sky around the fitted path
MIN_HALF, MAX_HALF = 36, 72  # patch half-size, half-res px (tile 292..580 px)
STACK = 3            # maps added along the motion
MAX_FRAMES = 40
LEAD_FRAMES = 4      # frames shown before the first hit
RANGE_SIGMA = 10.0   # 255 on the PNG


def movie(camera: str, d: dict, out_dir: Path) -> dict:
    """Build (or reuse) the movie for track summary `d` (state.json entry)."""
    key = f"{d['id']}_{d['n_hits']}"
    png, js = out_dir / f"{key}.png", out_dir / f"{key}.json"
    if js.exists() and png.exists():
        return json.loads(js.read_text())
    hits = d["hits"]
    t = np.array([_ts(h["t"]) for h in hits])
    xs, ys = np.array([h["x"] for h in hits], float), np.array([h["y"] for h in hits], float)
    if len(hits) >= 2 and np.ptp(t) > 0:
        fx, fy = np.polyfit(t / FRAME_SECONDS, xs, 1), np.polyfit(t / FRAME_SECONDS, ys, 1)
    else:
        vx, vy = d["velocity"]
        fx = np.array([vx, xs[0] - vx * t[0] / FRAME_SECONDS])
        fy = np.array([vy, ys[0] - vy * t[0] / FRAME_SECONDS])
    vx, vy = float(fx[0]), float(fy[0])
    t_show0, t_last = t[0] - LEAD_FRAMES * FRAME_SECONDS, t[-1]

    idx = FrameIndex(camera, datetime.fromtimestamp(t_show0).date() - timedelta(days=1),
                     datetime.fromtimestamp(t_last).date())
    good = [(p, tt.timestamp()) for p, tt in zip(idx.paths, idx.times)
            if t_show0 - (STACK + 1) * FRAME_SECONDS <= tt.timestamp() <= t_last + 60
            and (m := A.load_meta(camera, p.name)) is not None and m.ok and A.sig_path(camera, p.name).exists()]
    shown = [g for g in good if g[1] >= t_show0 - 60]
    if not shown:
        raise ValueError("no processed frames for this track")
    if len(shown) > MAX_FRAMES:
        shown = [shown[i] for i in np.linspace(0, len(shown) - 1, MAX_FRAMES).round().astype(int)]

    # fixed patch: centred on the middle of the fitted path, big enough for all of it
    ta, tb = shown[0][1] / FRAME_SECONDS, shown[-1][1] / FRAME_SECONDS
    xa, xb = np.polyval(fx, ta), np.polyval(fx, tb)
    ya, yb = np.polyval(fy, ta), np.polyval(fy, tb)
    cx, cy = (xa + xb) / 2, (ya + yb) / 2                       # full-res report coords
    half = int(np.clip(math.ceil(max(abs(xb - xa), abs(yb - ya)) / 4) + MARGIN, MIN_HALF, MAX_HALF))
    size = 2 * half + 1
    tile = size * ZOOM

    cache: dict[str, np.ndarray] = {}

    def sig(name: str) -> np.ndarray:
        if name not in cache:
            cache[name] = A.load_sig(camera, name)
            if len(cache) > STACK + 2:
                del cache[next(iter(cache))]
        return cache[name]

    hit_times = set(np.round(t / 60).astype(int))
    ncol = math.ceil(math.sqrt(len(shown)))
    nrow = math.ceil(len(shown) / ncol)
    sheet = np.zeros((nrow * tile, ncol * tile), np.uint8)
    frames = []
    for k, (p, ts) in enumerate(shown):
        acc, n = np.zeros((size, size), np.float32), 0
        for q, tq in reversed(good):
            if tq > ts + 1:
                continue
            dt = (ts - tq) / FRAME_SECONDS
            acc += _cut(sig(q.name), cx - vx * dt, cy - vy * dt, half)
            n += 1
            if n >= STACK:
                break
        img = np.clip(acc / np.sqrt(max(n, 1)) / RANGE_SIGMA, 0, 1)
        r, c = divmod(k, ncol)
        sheet[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile] = np.repeat(np.repeat((img * 255).astype(np.uint8), ZOOM, 0), ZOOM, 1)
        px, py = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
        frames.append({"name": p.name[9:24], "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                       "x": round(((px - cx) / 2 + half) * ZOOM + ZOOM / 2, 1),
                       "y": round(((py - cy) / 2 + half) * ZOOM + ZOOM / 2, 1),
                       "hit": int(round(ts / 60)) in hit_times, "stacked": n})
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{d['id']}_*"):
        old.unlink()
    write_png(png, sheet)
    meta = {"png": f"{camera}/movies/{png.name}", "tile": tile, "cols": ncol, "zoom": ZOOM,
            "range_sigma": RANGE_SIGMA, "frames": frames, "centre": [round(cx, 1), round(cy, 1)],
            "half_fullres": half * 2}
    js.write_text(json.dumps(meta))
    return meta


def _cut(sig: np.ndarray, x: float, y: float, half: int) -> np.ndarray:
    """(2 half + 1)^2 patch of the half-res map centred on full-res report (x, y),
    in report orientation (row 0 = top); off-map pixels are 0."""
    h, w = sig.shape
    col, row = coords.report_to_fits(x / 2, y / 2, h)
    c, r = int(round(col)), int(round(row))
    out = np.zeros((2 * half + 1, 2 * half + 1), np.float32)
    r0, r1, c0, c1 = r - half, r + half + 1, c - half, c + half + 1
    rr0, cc0, rr1, cc1 = max(r0, 0), max(c0, 0), min(r1, h), min(c1, w)
    if rr0 < rr1 and cc0 < cc1:
        out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = np.nan_to_num(sig[rr0:rr1, cc0:cc1], nan=0.0)
    return np.flipud(out)


def _ts(s: str) -> float:
    return datetime.fromisoformat(s).timestamp()
