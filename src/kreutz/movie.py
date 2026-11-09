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
import fcntl
import math
import warnings
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from . import coords, paths
from .detect import frames as A
from .detect.velocity import FRAME_SECONDS
from .frameindex import FrameIndex
from .inspect import write_png

ZOOM = 4             # screen px per half-res map px (= 2x the camera's full resolution) while the path fits
ZOOMS = (4, 2, 1)    # tried in turn until the whole path fits the tile
MARGIN = 20          # half-res px of sky around the fitted path
MIN_HALF, MAX_HALF = 36, 72  # patch half-size, half-res px (tile 292..580 px)
STACK = 8            # maps added along the motion (the detector stacks 8); 3 is offered too
MAX_FRAMES = 120     # frames in the patch movie, the newest (30 h); every frame, none skipped
LEAD_FRAMES = 4      # frames shown before the first hit
RANGE_SIGMA = 10.0   # 255 on the PNG
FULL_FRAMES = 120    # the field movie: this many frames up to the newest hit (30 h), at the camera's full resolution
FULL_RING = 16       # radius of the marker circle on it, px
FIELD_VERSION = 9    # in the cache key: bump when the field rendering changes, or old frames are served
FIELD_CLEAR_X, FIELD_CLEAR_Y = 8, 56  # half-res px: the field view's corner nearest the Sun sits this far from it across
#                        and down. Chris Wage's CCOR-1 frame of 2026-11-08, registered on ours through the comet's
#                        positions (0.76 screen px per camera px), starts 15 px right of the Sun and 111 px below it and
#                        is 1053 x 729 camera px: the arm's edge and the occulter's limb are in, for context. At 100 px
#                        on each axis (until 2026-11-09) ours showed 184 camera px less of the inner corona.
FIELD_MAX = 640      # half-res px: the view runs from that corner out to the field edge, at most this far
FIELD_MARGIN = 50    # half-res px around the object's path (always inside the view)
FIELD_BG_HOURS = 24  # the field's background: the pixelwise median of the good frames over this long before each
FIELD_BG_STEP = 4    # shown frame, every this many frames (so it is recomputed once per 4 shown frames). The static
#                      corona and glare cancel to zero; streamers that have moved show bright where they are and
#                      dark where they were, stars and the object stay. This is the hunters' recipe (frame minus
#                      the median of earlier frames); the minimum used until 2026-11-09 left every change positive,
#                      a grey haze that burned out to white near the occulter.
FIELD_LO, FIELD_HI = -2.2, 22.8  # the field's one linear scale (0 and 255 on the PNG) in units of the noise in the
#                      outer half of the view. Not divided by the noise per radius: the grain grows toward the Sun
#                      (4-5x by the occulter), as on the hunters' movies, and zero sits at 9% grey so the sky is black.
FIELD_SHOULDER = ((10.0, None), (92.0, 150.0), (600.0, 250.0), (660.0, 255.0))  # (value, PNG level): above the first
#                      value the scale runs straight in log(value) between these points instead. The Moon's earthlit
#                      face reads 92-553 (1st-99th percentile, CCOR-1 2026-11-08) and gets 100 levels in mid-grey, so
#                      its maria show; bright stars (10-90) sit at 124-150 instead of white; a comet's head is white.
FOLLOW_FRAMES = 8    # both movies run this many frames (2 h) past the last hit, the marker carried along the path:
#                      an object is watched until it is gone, not until the detector's last hit
END_HITS = 8         # hits at each end of a track whose straight-line fit carries the marker beyond the hits


def _path(t: np.ndarray, xs: np.ndarray, ys: np.ndarray, velocity) -> "callable":
    """pos(ts) -> (x, y), full-res report px: where the object is at time ts. Between
    its measured hits it is interpolated from them; outside, it is carried along the
    straight-line fit of the END_HITS hits at that end. One straight line through a
    whole long track misses: a Kreutz comet speeds up as it falls (20261107T1730_1924_1608
    went from 7 to 10 px/frame over 28 h, and the single fit sat up to 29 px off its hits)."""
    u = t / FRAME_SECONDS

    def end(sl: slice) -> tuple[float, float]:
        if len(u[sl]) >= 2 and np.ptp(u[sl]) > 0:
            return float(np.polyfit(u[sl], xs[sl], 1)[0]), float(np.polyfit(u[sl], ys[sl], 1)[0])
        return float(velocity[0]), float(velocity[1])

    head, tail = end(slice(0, END_HITS)), end(slice(-END_HITS, None))

    def pos(ts: float) -> tuple[float, float]:
        q = ts / FRAME_SECONDS
        if q <= u[0]:
            return float(xs[0] + head[0] * (q - u[0])), float(ys[0] + head[1] * (q - u[0]))
        if q >= u[-1]:
            return float(xs[-1] + tail[0] * (q - u[-1])), float(ys[-1] + tail[1] * (q - u[-1]))
        return float(np.interp(q, u, xs)), float(np.interp(q, u, ys))

    return pos


_LOCK_DEPTH = 0


@contextmanager
def build_lock():
    """One movie or package build at a time across processes (the dashboard server builds
    on request, the watcher when it alerts): they write the same files and the same git
    checkout. Re-entrant within a process: a package build holds it while building movies."""
    global _LOCK_DEPTH
    if _LOCK_DEPTH:
        _LOCK_DEPTH += 1
        try:
            yield
        finally:
            _LOCK_DEPTH -= 1
        return
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.STATE_DIR / "build.lock", "w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        _LOCK_DEPTH = 1
        try:
            yield
        finally:
            _LOCK_DEPTH = 0
            fcntl.flock(f, fcntl.LOCK_UN)


def movie(camera: str, d: dict, out_dir: Path, stack: int = STACK, full: bool = False) -> dict:
    with build_lock():
        return _movie(camera, d, out_dir, stack, full)


def _movie(camera: str, d: dict, out_dir: Path, stack: int = STACK, full: bool = False) -> dict:
    """Build (or reuse) the movie for track summary `d` (state.json entry), each
    frame the sum of the last `stack` maps along the motion. `full` gives the field
    movie (`_field`: the object's quadrant at full resolution, one PNG per frame)
    instead of the zoomed patch."""
    hits = sorted(d["hits"], key=lambda h: _ts(h["t"]))
    t = np.array([_ts(h["t"]) for h in hits])
    xs, ys = np.array([h["x"] for h in hits], float), np.array([h["y"] for h in hits], float)
    pos = _path(t, xs, ys, d["velocity"])
    t_show0, t_last = t[0] - LEAD_FRAMES * FRAME_SECONDS, t[-1] + FOLLOW_FRAMES * FRAME_SECONDS

    idx = FrameIndex(camera, datetime.fromtimestamp(t_show0).date() - timedelta(days=1),
                     datetime.fromtimestamp(t_last).date() + timedelta(days=1))
    good = [(p, tt.timestamp()) for p, tt in zip(idx.paths, idx.times)
            if t_show0 - (FIELD_BG_HOURS * 3600 if full else (stack + 1) * FRAME_SECONDS) - 60 <= tt.timestamp() <= t_last + 60
            and (m := A.load_meta(camera, p.name)) is not None and m.ok and A.sig_path(camera, p.name).exists()]
    shown = [g for g in good if g[1] >= t_show0 - 60]
    if not shown:
        raise ValueError("no processed frames for this track")
    # cached per track size and per number of frames that have arrived since its last hit
    key = f"{d['id']}_{d['n_hits']}_" + (f"full{FIELD_VERSION}" if full else f"s{stack}") + f"f{sum(ts > t[-1] + 60 for _, ts in shown)}"
    png, js = out_dir / f"{key}.png", out_dir / f"{key}.json"
    if js.exists():
        meta = json.loads(js.read_text())
        if png.exists() or (full and all((out_dir / Path(p).name).exists() for p in meta.get("pngs", []))):
            return meta
    hit_times = set(np.round(t / 60).astype(int))
    if full:
        return _field(camera, d, out_dir, key, js, good, shown[-FULL_FRAMES:], pos, hit_times)

    # The patch is fixed on the sky and holds the whole path: at ZOOM while that fits a ~580 px tile,
    # otherwise at the largest of ZOOMS that does (a 28-h track is 940 camera px long: zoom 1). Only a
    # path too long even for that is cut to its newest stretch. Every frame is shown, up to MAX_FRAMES.
    shown = shown[-MAX_FRAMES:]
    pts = [pos(ts) for _, ts in shown]

    def box(k0: int) -> tuple[float, float, float, float]:
        return (min(x for x, _ in pts[k0:]), max(x for x, _ in pts[k0:]), min(y for _, y in pts[k0:]), max(y for _, y in pts[k0:]))

    def cap(z: int) -> int:  # the patch half-size, map px, that keeps the tile near 580 px at zoom z
        return MAX_HALF * ZOOM // z

    lox, hix, loy, hiy = box(0)
    zoom = next((z for z in ZOOMS if math.ceil(max(hix - lox, hiy - loy) / 4) + MARGIN <= cap(z)), None)
    truncated = zoom is None
    if truncated:
        zoom, k0 = ZOOMS[-1], len(shown) - 1
        while k0 > 0:
            bx = box(k0 - 1)
            if math.ceil(max(bx[1] - bx[0], bx[3] - bx[2]) / 4) + MARGIN > cap(zoom):
                break
            k0 -= 1
        shown, pts = shown[k0:], pts[k0:]
        lox, hix, loy, hiy = box(0)
    cx, cy = (lox + hix) / 2, (loy + hiy) / 2                   # full-res report coords
    half = int(np.clip(math.ceil(max(hix - lox, hiy - loy) / 4) + MARGIN, MIN_HALF, cap(zoom)))
    size = 2 * half + 1
    tile = size * zoom

    cache: dict[str, np.ndarray] = {}

    def sig(name: str) -> np.ndarray:
        if name not in cache:
            cache[name] = A.load_sig(camera, name)
            if len(cache) > stack + 2:
                del cache[next(iter(cache))]
        return cache[name]

    ncol = math.ceil(math.sqrt(len(shown)))
    nrow = math.ceil(len(shown) / ncol)
    sheet = np.zeros((nrow * tile, ncol * tile), np.uint8)
    frames = []
    for k, (p, ts) in enumerate(shown):
        px, py = pts[k]
        acc, n = np.zeros((size, size), np.float32), 0
        for q, tq in reversed(good):
            if tq > ts + 1:
                continue
            qx, qy = pos(tq)  # added along the measured path, not one velocity: a comet speeds up
            acc += _cut(sig(q.name), cx - (px - qx), cy - (py - qy), half)
            n += 1
            if n >= stack:
                break
        stk = acc / np.sqrt(max(n, 1))
        img = np.clip(stk / RANGE_SIGMA, 0, 1)
        r, c = divmod(k, ncol)
        sheet[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile] = np.repeat(np.repeat((img * 255).astype(np.uint8), zoom, 0), zoom, 1)
        ri, ci = int(round((py - cy) / 2 + half)), int(round((px - cx) / 2 + half))
        ring = stk[max(ri - 3, 0):ri + 4, max(ci - 3, 0):ci + 4]
        frames.append({"peak": round(float(ring.max()), 1) if ring.size else None,  # sigma at the ring
                       "tail": round(float(np.percentile(stk, 99.9)), 1),          # the patch's own bright tail
"name": p.name[9:24], "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                       "x": round(((px - cx) / 2 + half) * zoom + zoom / 2, 1),
                       "y": round(((py - cy) / 2 + half) * zoom + zoom / 2, 1),
                       "hit": int(round(ts / 60)) in hit_times, "stacked": n})
    out_dir.mkdir(parents=True, exist_ok=True)
    _drop_stale(out_dir, d, key)
    write_png(png, sheet)
    meta = {"png": f"{camera}/movies/{png.name}", "tile": tile, "cols": ncol, "zoom": zoom, "stack": stack,
            "range_sigma": RANGE_SIGMA, "frames": frames, "centre": [round(cx, 1), round(cy, 1)],
            "half_fullres": half * 2, "truncated": truncated}
    js.write_text(json.dumps(meta))
    return meta


def _field(camera: str, d: dict, out_dir: Path, key: str, js: Path, good: list, shown: list, pos, hit_times: set) -> dict:
    """The field movie: one PNG per shown frame, the object's quadrant (`_field_crop`) of
    the raw frame at full resolution minus the median of the earlier frames, no stacking
    (shifting along one object's motion smears everything else), no smoothing, on one
    linear scale in units of the outer field's noise. The hunters' recipe and framing."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pts = [pos(ts) for _, ts in shown]
    h, w = _raw_full(shown[0][0]).shape
    sun = next((tuple(m.sun_xy) for p, _ in shown if (m := A.load_meta(camera, p.name)) is not None and m.sun_xy), None)
    x0, x1, y0, y1 = (2 * v for v in _field_crop([x / 2 for x, _ in pts], [y / 2 for _, y in pts],
                                                 (sun[0] / 2, sun[1] / 2) if sun else None, w // 2, h // 2))
    far = None  # the outer half of the view (by distance from the Sun), where the noise is measured
    order = {p.name: i for i, (p, _) in enumerate(good)}
    crops: dict[str, np.ndarray] = {}

    def crop(q: Path) -> np.ndarray:
        if q.name not in crops:
            crops[q.name] = _raw_full(q)[y0:y1, x0:x1].copy()
        return crops[q.name]

    frames, pngs, bg, bg_names = [], [], None, None
    for k, (p, ts) in enumerate(shown):
        earlier = [q for q, tq in good if ts - FIELD_BG_HOURS * 3600 - 60 <= tq < ts - 60]
        if len(earlier) > 2 * FIELD_BG_STEP:
            earlier = [q for q in earlier if order[q.name] % FIELD_BG_STEP == 0]
        if not earlier:
            raise ValueError("no earlier frames for the background")
        names = tuple(q.name for q in earlier)
        if names != bg_names:
            stack = np.stack([crop(q) for q in earlier])
            for n in [n for n in crops if n not in names]:
                del crops[n]
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # all-NaN columns: outside the field
                bg = np.nanmedian(stack, axis=0)
            del stack
            bg_names = names
        cur = crops[p.name] if p.name in crops else _raw_full(p)[y0:y1, x0:x1]
        with np.errstate(all="ignore"):
            diff = cur - bg
        ok = np.isfinite(diff)
        if far is None:
            far = np.ones(ok.shape, bool)
            if sun is not None and ok.any():
                yy, xx = np.mgrid[y0:y1, x0:x1]
                r = np.hypot(xx - sun[0], yy - sun[1])
                far = r > np.median(r[ok])
        sel = ok & far
        v = diff[sel] if sel.sum() >= 1000 else diff[ok]
        s = 1.4826 * float(np.median(np.abs(v - np.median(v)))) if v.size else 0.0
        z = np.where(ok, diff / (s if s > 0 else 1.0), np.nan)
        img = np.where(ok, _levels(z), 0).astype(np.uint8)
        px, py = pts[k]
        ri, ci = int(round(py - y0)), int(round(px - x0))
        ring = z[max(ri - 6, 0):ri + 7, max(ci - 6, 0):ci + 7]
        name = p.name[9:24]
        stamp(img, f"{name[:4]}-{name[4:6]}-{name[6:8]} {name[9:11]}:{name[11:13]} UTC")
        fpng = out_dir / f"{key}_{k:03d}.png"
        write_png(fpng, img)
        pngs.append(f"{camera}/movies/{fpng.name}")
        frames.append({"peak": round(float(np.nanmax(ring)), 1) if np.isfinite(ring).any() else None,
                       "tail": round(float(np.percentile(z[ok], 99.9)), 1) if ok.any() else None,
                       "name": name, "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                       "x": round(px - x0, 1), "y": round(py - y0, 1),
                       "hit": int(round(ts / 60)) in hit_times, "stacked": 1, "background": len(names)})
    _drop_stale(out_dir, d, key)
    meta = {"pngs": pngs, "width": x1 - x0, "height": y1 - y0, "crop": [x0, y0], "zoom": 1, "stack": 1,
            "range_sigma": FIELD_HI, "lo_sigma": FIELD_LO, "hi_sigma": FIELD_HI, "frames": frames, "ring_r": FULL_RING}
    js.write_text(json.dumps(meta))
    return meta


def _drop_stale(out_dir: Path, d: dict, key: str) -> None:
    """Remove the track's movies of other sizes, and this kind's with another count of follow frames."""
    kind = key[len(f"{d['id']}_{d['n_hits']}_"):].rsplit("f", 1)[0]  # "s8" or "full9"
    for old in out_dir.glob(f"{d['id']}_*"):
        rest = old.name[len(d["id"]) + 1:]
        same_size = rest.startswith(f"{d['n_hits']}_")
        if not same_size or (rest.startswith(f"{d['n_hits']}_{kind}f") and not old.name.startswith(key + ".") and not old.name.startswith(key + "_")):
            old.unlink()


def _levels(z: np.ndarray) -> np.ndarray:
    """PNG levels of the field's values: linear from FIELD_LO to the shoulder's first
    value, then straight in log(value) through FIELD_SHOULDER."""
    k0 = FIELD_SHOULDER[0][0]
    lin = (z - FIELD_LO) / (FIELD_HI - FIELD_LO) * 255
    xs = [math.log(v) for v, _ in FIELD_SHOULDER]
    ys = [(k0 - FIELD_LO) / (FIELD_HI - FIELD_LO) * 255] + [lv for _, lv in FIELD_SHOULDER[1:]]
    with np.errstate(all="ignore"):
        top = np.interp(np.log(np.maximum(z, k0)), xs, ys)
    return np.clip(np.where(z <= k0, lin, top), 0, 255)


def _field_crop(xs: list[float], ys: list[float], sun: tuple[float, float] | None, w: int, h: int) -> tuple[int, int, int, int]:
    """The field view's crop (x0, x1, y0, y1) in half-res px: the quadrant the object
    is in, as the hunters frame their movies. The corner nearest the Sun sits
    FIELD_CLEAR_X px across and FIELD_CLEAR_Y px down from it, so the occulter's
    limb and the streamer bases show; the view runs out to
    the field edge on the object's side, at most FIELD_MAX px, and always holds
    the fitted path with FIELD_MARGIN around it. Without a Sun position: the
    path with its margin."""
    lo_x, hi_x = min(xs) - FIELD_MARGIN, max(xs) + FIELD_MARGIN
    lo_y, hi_y = min(ys) - FIELD_MARGIN, max(ys) + FIELD_MARGIN
    if sun is not None:
        sx, sy = sun
        ox, oy = float(np.mean(xs)), float(np.mean(ys))
        if ox >= sx:
            lo_x = min(lo_x, sx + FIELD_CLEAR_X)
            hi_x = max(hi_x, min(w, lo_x + FIELD_MAX))
        else:
            hi_x = max(hi_x, sx - FIELD_CLEAR_X)
            lo_x = min(lo_x, max(0, hi_x - FIELD_MAX))
        if oy >= sy:
            lo_y = min(lo_y, sy + FIELD_CLEAR_Y)
            hi_y = max(hi_y, min(h, lo_y + FIELD_MAX))
        else:
            hi_y = max(hi_y, sy - FIELD_CLEAR_Y)
            lo_y = min(lo_y, max(0, hi_y - FIELD_MAX))
    x0 = int(np.clip(math.floor(lo_x), 0, w - 1))
    x1 = int(np.clip(math.ceil(hi_x), x0 + 1, w))
    y0 = int(np.clip(math.floor(lo_y), 0, h - 1))
    y1 = int(np.clip(math.ceil(hi_y), y0 + 1, h))
    return x0, x1, y0, y1


# A 5x7 bitmap font for the frame stamp: digits, '-', ':', space and 'UTC'.
_FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"],
    ":": ["00000", "00100", "00000", "00000", "00000", "00100", "00000"],
    " ": ["00000"] * 7,
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"],
    "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "C": ["01110", "10001", "10000", "10000", "10000", "10001", "01110"],
}
STAMP_SCALE = 2


def stamp(img: np.ndarray, text: str, x: int = 6, y: int = 6, scale: int = STAMP_SCALE) -> None:
    """Write `text` into the top-left of a uint8 image in place: white glyphs on a
    dark halo, so it reads on any background."""
    cols = len(text) * 6 * scale
    rows = 7 * scale
    y1, x1 = min(y + rows, img.shape[0]), min(x + cols, img.shape[1])
    if y1 <= y or x1 <= x:
        return
    glyphs = np.zeros((rows, cols), bool)
    for i, ch in enumerate(text):
        g = _FONT.get(ch, _FONT[" "])
        for r, line in enumerate(g):
            for c, bit in enumerate(line):
                if bit == "1":
                    glyphs[r * scale:(r + 1) * scale, (i * 6 + c) * scale:(i * 6 + c + 1) * scale] = True
    from scipy import ndimage

    halo = ndimage.binary_dilation(glyphs, iterations=scale)
    region = img[y:y1, x:x1]
    region[halo[:y1 - y, :x1 - x]] = 0
    region[glyphs[:y1 - y, :x1 - x]] = 255


def _raw_full(p: Path) -> np.ndarray:
    """The raw frame at full resolution, report orientation (row 0 = top), masked pixels NaN."""
    from .fitsio import read_frame

    d = read_frame(p, check_orientation=False).data.astype(np.float32)
    return np.flipud(np.where(d > 0, d, np.nan))  # the occulter and the arm read 0 or below


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
