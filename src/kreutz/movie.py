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
import warnings
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
STACK = 8            # maps added along the motion (the detector stacks 8); 3 is offered too
MAX_FRAMES = 40
LEAD_FRAMES = 4      # frames shown before the first hit
RANGE_SIGMA = 10.0   # 255 on the PNG
FULL_FRAMES = 32     # the field movie: the frame at half res, this many frames up to the newest hit
FULL_RING = 10       # marker size on it (crosshair tick length), px
FIELD_VERSION = 5    # in the cache key: bump when the field rendering changes, or old frames are served
FIELD_SUN_CLEAR = 100  # half-res px: the field view's corner nearest the Sun sits this far from it on each axis,
#                        just outside the occulter (the hunters' framing: glare and arm out, streamer bases in)
FIELD_MAX = 640      # half-res px: the view runs from that corner out to the field edge, at most this far
FIELD_MARGIN = 50    # half-res px around the object's path (always inside the view)
FIELD_BG_HOURS = 24  # the field's background: the pixelwise minimum of the good frames over this long before the
FIELD_BG_STEP = 4    # first shown frame, every this many frames. The static corona and glare cancel; streamers
#                      (which move) and stars stay, which is the look of the hunters' own field movies.
FIELD_SOFT = 1.0     # Gaussian smoothing of the field, half-res px: noise becomes grain, stars and comet soft dots
FIELD_LO, FIELD_HI = -1.0, 12.0  # the field's linear scale in sigma of the frame's own noise (0 and 255 on the PNG)


def movie(camera: str, d: dict, out_dir: Path, stack: int = STACK, full: bool = False) -> dict:
    """Build (or reuse) the movie for track summary `d` (state.json entry), each
    frame the sum of the last `stack` maps along the motion. `full` gives the
    whole field at half resolution (one PNG per frame) instead of the zoomed patch."""
    key = f"{d['id']}_{d['n_hits']}_full{FIELD_VERSION}" if full else f"{d['id']}_{d['n_hits']}_s{stack}"
    png, js = out_dir / f"{key}.png", out_dir / f"{key}.json"
    if js.exists():
        meta = json.loads(js.read_text())
        if png.exists() or (full and all((out_dir / Path(p).name).exists() for p in meta.get("pngs", []))):
            return meta
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
            if t_show0 - (FIELD_BG_HOURS * 3600 if full else (stack + 1) * FRAME_SECONDS) - 60 <= tt.timestamp() <= t_last + 60
            and (m := A.load_meta(camera, p.name)) is not None and m.ok and A.sig_path(camera, p.name).exists()]
    shown = [g for g in good if g[1] >= t_show0 - 60]
    if not shown:
        raise ValueError("no processed frames for this track")
    if full:
        shown = shown[-FULL_FRAMES:]
    elif len(shown) > MAX_FRAMES:
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
            if len(cache) > stack + 2:
                del cache[next(iter(cache))]
        return cache[name]

    hit_times = set(np.round(t / 60).astype(int))
    if full:
        # The field is the raw frame minus one fixed background, the pixelwise minimum
        # of the good frames over the FIELD_BG_HOURS before the first shown frame: the
        # static corona and glare cancel, streamers and stars stay. No stacking (shifting
        # along one object's motion smears everything else). Divided by the frame's own
        # noise per radius, smoothed by FIELD_SOFT px, on a linear scale from FIELD_LO
        # sigma so the noise shows as grain rather than being cut to black. Cropped to
        # the object's quadrant, from just outside the occulter out to the field edge.
        out_dir.mkdir(parents=True, exist_ok=True)
        frames, pngs, imgs = [], [], []
        h, w = None, None
        bg_frames = [q for q, tq in good if tq < shown[0][1] - 60][::-1][::FIELD_BG_STEP][::-1]
        if not bg_frames:
            raise ValueError("no earlier frames for the background")
        bg = None
        for q in bg_frames:
            a = _raw_half(q).astype(np.float32)
            bg = a if bg is None else np.fmin(bg, a)  # fmin ignores NaN

        for k, (p, ts) in enumerate(shown):
            cur = _raw_half(p).astype(np.float32)
            with np.errstate(all="ignore"):
                diff = cur - bg
            ok = np.isfinite(diff)
            m = A.load_meta(camera, p.name)
            centre = (m.sun_xy[0] / 2, m.sun_xy[1] / 2) if m is not None and m.sun_xy else None
            z = _soften(np.where(ok, diff / _radial_sigma(diff, ok, centre), 0.0), ok, FIELD_SOFT)
            ok &= np.isfinite(z)
            z = np.where(ok, z, FIELD_LO)
            img = (np.clip((z - FIELD_LO) / (FIELD_HI - FIELD_LO), 0, 1) * 255).astype(np.uint8)
            h, w = img.shape
            imgs.append((img, centre))
            px, py = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
            ri, ci = int(round(py / 2)), int(round(px / 2))
            ring = z[max(ri - 3, 0):ri + 4, max(ci - 3, 0):ci + 4]
            frames.append({"peak": round(float(ring.max()), 1) if ring.size else None,
                           "tail": round(float(np.percentile(z[ok], 99.9)), 1) if ok.any() else None,
                           "name": p.name[9:24], "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                           "x": round(px / 2, 1), "y": round(py / 2, 1),
                           "hit": int(round(ts / 60)) in hit_times, "stacked": 1, "background": len(bg_frames)})
        x0, x1, y0, y1 = _field_crop([f["x"] for f in frames], [f["y"] for f in frames],
                                      next((c for _, c in imgs if c), None), w, h)
        for k, ((img, _), f) in enumerate(zip(imgs, frames)):
            fpng = out_dir / f"{key}_{k:02d}.png"
            tile = np.ascontiguousarray(img[y0:y1, x0:x1])
            stamp(tile, f"{f['name'][:4]}-{f['name'][4:6]}-{f['name'][6:8]} {f['name'][9:11]}:{f['name'][11:13]} UTC")
            write_png(fpng, tile)
            pngs.append(f"{camera}/movies/{fpng.name}")
            f["x"], f["y"] = round(f["x"] - x0, 1), round(f["y"] - y0, 1)
        for old in out_dir.glob(f"{d['id']}_*"):
            if not old.name.startswith(f"{d['id']}_{d['n_hits']}_"):
                old.unlink()
        meta = {"pngs": pngs, "width": x1 - x0, "height": y1 - y0, "crop": [x0, y0], "zoom": 1, "stack": 1,
                "range_sigma": FIELD_HI, "lo_sigma": FIELD_LO, "hi_sigma": FIELD_HI, "frames": frames, "ring_r": FULL_RING}
        js.write_text(json.dumps(meta))
        return meta
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
            if n >= stack:
                break
        stk = acc / np.sqrt(max(n, 1))
        img = np.clip(stk / RANGE_SIGMA, 0, 1)
        r, c = divmod(k, ncol)
        sheet[r * tile:(r + 1) * tile, c * tile:(c + 1) * tile] = np.repeat(np.repeat((img * 255).astype(np.uint8), ZOOM, 0), ZOOM, 1)
        px, py = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
        ri, ci = int(round((py - cy) / 2 + half)), int(round((px - cx) / 2 + half))
        ring = stk[max(ri - 3, 0):ri + 4, max(ci - 3, 0):ci + 4]
        frames.append({"peak": round(float(ring.max()), 1) if ring.size else None,  # sigma at the ring
                       "tail": round(float(np.percentile(stk, 99.9)), 1),          # the patch's own bright tail
"name": p.name[9:24], "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                       "x": round(((px - cx) / 2 + half) * ZOOM + ZOOM / 2, 1),
                       "y": round(((py - cy) / 2 + half) * ZOOM + ZOOM / 2, 1),
                       "hit": int(round(ts / 60)) in hit_times, "stacked": n})
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob(f"{d['id']}_*"):
        if not old.name.startswith(f"{d['id']}_{d['n_hits']}_"):
            old.unlink()
    write_png(png, sheet)
    meta = {"png": f"{camera}/movies/{png.name}", "tile": tile, "cols": ncol, "zoom": ZOOM, "stack": stack,
            "range_sigma": RANGE_SIGMA, "frames": frames, "centre": [round(cx, 1), round(cy, 1)],
            "half_fullres": half * 2}
    js.write_text(json.dumps(meta))
    return meta


RADIAL_STEP = 8  # px annuli for the field's noise estimate


def _field_crop(xs: list[float], ys: list[float], sun: tuple[float, float] | None, w: int, h: int) -> tuple[int, int, int, int]:
    """The field view's crop (x0, x1, y0, y1) in half-res px: the quadrant the object
    is in, as the hunters frame their movies. The corner nearest the Sun sits
    FIELD_SUN_CLEAR px from it on each axis, just outside the occulter, so the
    glare and the arm stay out and the streamer bases show; the view runs out to
    the field edge on the object's side, at most FIELD_MAX px, and always holds
    the fitted path with FIELD_MARGIN around it. Without a Sun position: the
    path with its margin."""
    lo_x, hi_x = min(xs) - FIELD_MARGIN, max(xs) + FIELD_MARGIN
    lo_y, hi_y = min(ys) - FIELD_MARGIN, max(ys) + FIELD_MARGIN
    if sun is not None:
        sx, sy = sun
        ox, oy = float(np.mean(xs)), float(np.mean(ys))
        if ox >= sx:
            lo_x = min(lo_x, sx + FIELD_SUN_CLEAR)
            hi_x = max(hi_x, min(w, lo_x + FIELD_MAX))
        else:
            hi_x = max(hi_x, sx - FIELD_SUN_CLEAR)
            lo_x = min(lo_x, max(0, hi_x - FIELD_MAX))
        if oy >= sy:
            lo_y = min(lo_y, sy + FIELD_SUN_CLEAR)
            hi_y = max(hi_y, min(h, lo_y + FIELD_MAX))
        else:
            hi_y = max(hi_y, sy - FIELD_SUN_CLEAR)
            lo_y = min(lo_y, max(0, hi_y - FIELD_MAX))
    x0 = int(np.clip(math.floor(lo_x), 0, w - 1))
    x1 = int(np.clip(math.ceil(hi_x), x0 + 1, w))
    y0 = int(np.clip(math.floor(lo_y), 0, h - 1))
    y1 = int(np.clip(math.ceil(hi_y), y0 + 1, h))
    return x0, x1, y0, y1


def _soften(z: np.ndarray, ok: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing that ignores NaN/masked pixels (normalised by the smoothed
    mask); pixels with too little valid support come back NaN."""
    from scipy import ndimage

    if sigma <= 0:
        return z
    f = ndimage.gaussian_filter(np.where(ok, z, 0.0), sigma)
    n = ndimage.gaussian_filter(ok.astype(np.float32), sigma)
    with np.errstate(all="ignore"):
        return np.where(n > 0.3, f / n, np.nan)


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


def _radial_sigma(diff: np.ndarray, ok: np.ndarray, centre: tuple[float, float] | None) -> np.ndarray:
    """Per-pixel noise of a difference frame as a function of distance from the Sun
    (photon noise follows the corona's brightness, which falls steeply with radius):
    a robust sigma (1.4826 MAD) in RADIAL_STEP-px annuli, interpolated across empty ones."""
    h, w = diff.shape
    cx, cy = centre if centre else (w / 2, h / 2)
    yy, xx = np.indices(diff.shape)
    r = (np.hypot(yy - cy, xx - cx) / RADIAL_STEP).astype(int)
    n = int(r.max()) + 1
    prof = np.full(n, np.nan)
    for k in range(n):
        sel = ok & (r == k)
        if sel.sum() >= 50:
            v = diff[sel]
            prof[k] = 1.4826 * np.median(np.abs(v - np.median(v)))
    good = np.isfinite(prof) & (prof > 0)
    if not good.any():
        return np.ones_like(diff)
    prof = np.interp(np.arange(n), np.flatnonzero(good), prof[good])
    floor = 1e-3 * float(np.median(prof[good]))  # raw units are tiny (1e-10): the floor must be relative
    return np.maximum(prof[r], floor).astype(np.float32)


def _raw_half(p: Path) -> np.ndarray:
    """The raw frame at half resolution (2x2 mean), report orientation (row 0 = top),
    masked pixels NaN."""
    from .fitsio import read_frame

    d = read_frame(p, check_orientation=False).data.astype(np.float32)
    d = np.where(d > 0, d, np.nan)  # the occulter and the arm read 0 or below
    h, w = (d.shape[0] // 2) * 2, (d.shape[1] // 2) * 2
    with np.errstate(all="ignore"):
        small = np.nanmean(d[:h, :w].reshape(h // 2, 2, w // 2, 2), axis=(1, 3))
    return np.flipud(small)


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
