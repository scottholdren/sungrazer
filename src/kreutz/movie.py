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
FULL_FRAMES = 16     # the full-field movie: the whole frame at half res, this many frames up to the newest hit
FULL_RING = 10       # ring radius on it, px
FIELD_BG = 5         # the field is the frame minus the median of this many earlier frames
FIELD_LO, FIELD_HI = -2.0, 8.0  # the field's linear scale in sigma of the frame's own noise (0 and 255 on the PNG)


def movie(camera: str, d: dict, out_dir: Path, stack: int = STACK, full: bool = False) -> dict:
    """Build (or reuse) the movie for track summary `d` (state.json entry), each
    frame the sum of the last `stack` maps along the motion. `full` gives the
    whole field at half resolution (one PNG per frame) instead of the zoomed patch."""
    key = f"{d['id']}_{d['n_hits']}_full" if full else f"{d['id']}_{d['n_hits']}_s{stack}"
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
            if t_show0 - (max(stack, FIELD_BG) + 1) * FRAME_SECONDS <= tt.timestamp() <= t_last + 60
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
        # The field is the raw frame minus the median of the previous FIELD_BG frames
        # (no stacking: shifting along one object's motion smears everything else),
        # on a linear scale from FIELD_LO sigma of the frame's own noise, so the noise
        # shows as texture rather than being cut to black.
        out_dir.mkdir(parents=True, exist_ok=True)
        frames, pngs = [], []
        raws: dict[str, np.ndarray] = {}

        def raw(p: Path) -> np.ndarray:
            if p.name not in raws:
                raws[p.name] = _raw_half(p)
                if len(raws) > FIELD_BG + 2:
                    del raws[next(iter(raws))]
            return raws[p.name]

        for k, (p, ts) in enumerate(shown):
            earlier = [q for q, tq in good if tq < ts - 1][-FIELD_BG:]
            if not earlier:
                raise ValueError("no earlier frames for the background")
            cur = raw(p)
            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns under the occulter
                diff = cur - np.nanmedian(np.stack([raw(q) for q in earlier]), axis=0)
            ok = np.isfinite(diff)
            m = A.load_meta(camera, p.name)
            centre = (m.sun_xy[0] / 2, m.sun_xy[1] / 2) if m is not None and m.sun_xy else None
            z = np.where(ok, diff / _radial_sigma(diff, ok, centre), FIELD_LO)
            img = (np.clip((z - FIELD_LO) / (FIELD_HI - FIELD_LO), 0, 1) * 255).astype(np.uint8)
            h, w = img.shape
            fpng = out_dir / f"{key}_{k:02d}.png"
            write_png(fpng, img)
            pngs.append(f"{camera}/movies/{fpng.name}")
            px, py = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
            ri, ci = int(round(py / 2)), int(round(px / 2))
            ring = z[max(ri - 3, 0):ri + 4, max(ci - 3, 0):ci + 4]
            frames.append({"peak": round(float(ring.max()), 1) if ring.size else None,
                           "tail": round(float(np.percentile(z[ok], 99.9)), 1) if ok.any() else None,
                           "name": p.name[9:24], "t": datetime.fromtimestamp(ts).astimezone().isoformat(timespec="minutes"),
                           "x": round(px / 2, 1), "y": round(py / 2, 1),
                           "hit": int(round(ts / 60)) in hit_times, "stacked": 1, "background": len(earlier)})
        for old in out_dir.glob(f"{d['id']}_*"):
            if not old.name.startswith(f"{d['id']}_{d['n_hits']}_"):
                old.unlink()
        meta = {"pngs": pngs, "width": w, "height": h, "zoom": 1, "stack": 1, "range_sigma": FIELD_HI,
                "lo_sigma": FIELD_LO, "hi_sigma": FIELD_HI, "frames": frames, "ring_r": FULL_RING}
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
