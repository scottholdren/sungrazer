"""Stage B: shift-and-stack the last N significance maps for every trial velocity.

An object moving at constant velocity v sits at p - v*dt_k in the frame taken
dt_k frames before the newest one. For each v we add up the N maps shifted
back along that path, so the object piles up at its newest position p while
noise averages away.

Cosmic rays: a hit lands in one frame only, so the brightest frame's value is
dropped from every sum: stack = (sum - max) / sqrt(N - 1), still in sigma
units. A real mover is bright in all frames and loses only 1/N of its signal;
a single-frame spike loses everything. (Earlier versions clipped each map at
+8 sigma instead, which capped every stack at 8*sqrt(8) = 22.6 sigma: all
bright objects tied there, so ranking by significance meant nothing.) Maps are
still clipped at -3 sigma so a masked-star hole can't cancel a real object.

Work is done on the 2x-downsampled maps (half resolution). Shifts use the real
time gaps between frames, so missing frames don't break the motion model.
`backend` is a hook for a GPU version later; only "numpy" exists.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from .velocity import FRAME_SECONDS, VelocityGrid

VERSION = "b2"  # bump when stacking or peak finding changes (cached peaks are keyed on it)
N_FRAMES = 8
MIN_FRAMES = 6  # a window needs this many good maps among its N
MAX_SPAN_FRAMES = 14  # ...spanning at most this many 15-min slots (allows gaps)
FLOOR = -3.0  # sigma; see module doc
PEAK_THRESHOLD = 5.0  # stacked sigma; deliberately low, tracking does the rest
MAX_PEAKS = 400
DS = 2  # downsample factor of stage-A maps


@dataclass
class Peak:
    t: float  # unix time of the window's newest frame
    x: float  # report coords, full resolution, at time t
    y: float
    vx: float  # report px / frame
    vy: float
    sig: float
    edge: float | None = None  # distance (full-res px) to the nearest large invalid region
    elong: float | None = None  # axis ratio of the blob under the peak in the newest frame


def _shift_add(acc: np.ndarray, mx: np.ndarray, m: np.ndarray, dr: int, dc: int) -> None:
    """acc[p] += m[p - (dr, dc)] and mx[p] = max(mx[p], m[p - (dr, dc)]) where in bounds."""
    h, w = m.shape
    if abs(dr) >= h or abs(dc) >= w:
        return
    a_r = slice(max(dr, 0), h + min(dr, 0))
    m_r = slice(max(-dr, 0), h - max(dr, 0))
    a_c = slice(max(dc, 0), w + min(dc, 0))
    m_c = slice(max(-dc, 0), w - max(dc, 0))
    src = m[m_r, m_c]
    acc[a_r, a_c] += src
    np.maximum(mx[a_r, a_c], src, out=mx[a_r, a_c])


def stack_window(maps: list[np.ndarray], times: list[float], grid: VelocityGrid,
                 backend: str = "numpy") -> tuple[np.ndarray, np.ndarray]:
    """Return (best significance, index of best velocity) per half-res pixel.

    maps[-1] is the newest frame; all positions refer to it.
    """
    if backend != "numpy":
        raise NotImplementedError(f"backend {backend!r}")
    floored = [np.maximum(np.nan_to_num(m, nan=0.0), FLOOR).astype(np.float32) for m in maps]
    dts = [(times[-1] - t) / FRAME_SECONDS for t in times]
    norm = 1.0 / np.sqrt(len(maps) - 1)
    best = np.full(maps[-1].shape, -np.inf, np.float32)
    arg = np.zeros(maps[-1].shape, np.int32)
    acc = np.empty_like(best)
    mx = np.empty_like(best)
    for vi, (vx, vy) in enumerate(zip(grid.vx, grid.vy)):
        # report (vx, vy) -> array (col, row) motion: col = x, row = H-1-y
        vcol, vrow = vx / DS, -vy / DS
        acc.fill(0)
        mx.fill(FLOOR)  # pixels no map reaches stay at sum 0 - FLOOR: harmless, below threshold
        for m, dt in zip(floored, dts):
            # object at p now was at p - v*dt then: acc[p] += m[p - v*dt]
            _shift_add(acc, mx, m, int(round(vrow * dt)), int(round(vcol * dt)))
        acc -= mx
        acc *= norm
        better = acc > best
        best[better] = acc[better]
        arg[better] = vi
    return best, arg


def edge_distance(newest: np.ndarray, min_area: int = 500) -> np.ndarray:
    """Distance (half-res px) to the nearest LARGE NaN region of a map (occulter,
    arm, outside the field) or the array border. Small NaN blobs (masked stars)
    don't count."""
    nan = ~np.isfinite(newest)
    lab, n = ndimage.label(nan)
    if n:
        sizes = np.bincount(lab.ravel())
        big = sizes >= min_area
        big[0] = False
        nan = big[lab]
    nan = np.pad(nan, 1, constant_values=True)  # the array edge is an edge too
    return ndimage.distance_transform_edt(~nan)[1:-1, 1:-1]


def elongation(sig: np.ndarray, r: int, c: int, half: int = 12, level: float = 2.5) -> float:
    """Axis ratio of the connected >level blob nearest (r, c) in a half-res map.

    A point source is round (ratio ~1-2); streamer edges and other linear
    structures that survive the DoG filter are long and thin (ratio >> 3).
    The box is 25 half-res px (50 full-res) across: a 13-px box cut streamer
    rays short, so they measured ~3 and slipped through (Jul 20-22 2025).
    """
    h, w = sig.shape
    r0, r1, c0, c1 = max(r - half, 0), min(r + half + 1, h), max(c - half, 0), min(c + half + 1, w)
    box = np.nan_to_num(sig[r0:r1, c0:c1], nan=0.0)
    lab, n = ndimage.label(box > level)
    if n == 0:
        return 1.0
    # the component closest to the peak
    cr, cc = r - r0, c - c0
    best, bd = 0, 1e9
    for k in range(1, n + 1):
        ys, xs = np.nonzero(lab == k)
        d = np.min(np.hypot(ys - cr, xs - cc))
        if d < bd:
            best, bd = k, d
    ys, xs = np.nonzero(lab == best)
    if len(ys) < 3:
        return 1.0
    cov = np.cov(np.vstack([xs, ys]))
    ev = np.sort(np.linalg.eigvalsh(cov))
    return float(np.sqrt(max(ev[1], 1e-6) / max(ev[0], 0.08)))


def find_peaks(best: np.ndarray, arg: np.ndarray, valid: np.ndarray, t: float, grid: VelocityGrid,
               height: int, threshold: float = PEAK_THRESHOLD, max_peaks: int = MAX_PEAKS,
               edge: np.ndarray | None = None, newest: np.ndarray | None = None) -> list[Peak]:
    b = np.where(valid, best, -np.inf)
    local = (b == ndimage.maximum_filter(b, size=5)) & (b > threshold)
    rows, cols = np.nonzero(local)
    order = np.argsort(b[rows, cols])[::-1][:max_peaks]
    out = []
    for r, c in zip(rows[order], cols[order]):
        # half-res pixel (r, c) covers full-res rows 2r..2r+1 -> centre 2r + 0.5
        x, y = coords_from_ds(c, r, height)
        vi = arg[r, c]
        e = None if edge is None else float(edge[r, c] * DS)
        el = None if newest is None else elongation(newest, r, c)
        out.append(Peak(t, x, y, float(grid.vx[vi]), float(grid.vy[vi]), float(b[r, c]), e, el))
    return out


def coords_from_ds(c: float, r: float, height: int) -> tuple[float, float]:
    from .. import coords

    return coords.fits_to_report(DS * c + (DS - 1) / 2, DS * r + (DS - 1) / 2, height)
