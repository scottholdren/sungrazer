"""Contact sheets: let a human vet a track by eye.

One column per frame, following the track's straight-line motion (fitted to its
hits) from the first frame of its first window to its last hit:
  top row     raw frame cutout (local stretch), report orientation (north up)
  bottom row  stage-A significance map (-2 .. +6 sigma)
Small tick marks at the tile edges point at the predicted position. A real
comet is a dot that stays on the ticks in both rows and gets brighter; noise
flickers, star residuals sit on a mask edge, and artefacts line up with the
occulter arm or the field edge.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from . import coords
from .detect import frames as A
from .detect.velocity import FRAME_SECONDS
from .fitsio import read_frame
from .inspect import write_png

HALF = 24  # cutout half-size, full-res px
SCALE = 3
MAX_COLS = 20


def contact_sheet(track: dict, out: Path) -> tuple[Path, list[str]]:
    """Write the sheet; return its path and the frame name of each column."""
    camera = track["camera"]
    hits = track["hits"]
    t = np.array([datetime.fromisoformat(h["frame_time"]).timestamp() for h in hits])
    xs, ys = np.array([h["x"] for h in hits]), np.array([h["y"] for h in hits])
    if len(hits) >= 2 and np.ptp(t) > 0:
        fx, fy = np.polyfit(t / FRAME_SECONDS, xs, 1), np.polyfit(t / FRAME_SECONDS, ys, 1)
    else:
        fx, fy = np.array([track["velocity"][0], xs[0] - track["velocity"][0] * t[0] / FRAME_SECONDS]), \
                 np.array([track["velocity"][1], ys[0] - track["velocity"][1] * t[0] / FRAME_SECONDS])
    t0 = t[0] - 7 * FRAME_SECONDS - 60
    from .detect import frame_list

    d0 = datetime.fromtimestamp(t0).date()
    d1 = datetime.fromtimestamp(t[-1]).date()
    paths = [p for p in frame_list(camera, d0, d1)
             if t0 <= _ts(p) <= t[-1] + 60 and (m := A.load_meta(camera, p.name)) is not None and m.ok]
    if len(paths) > MAX_COLS:
        paths = [paths[i] for i in np.linspace(0, len(paths) - 1, MAX_COLS).round().astype(int)]
    tile = (2 * HALF + 1) * SCALE
    sheet = np.full((2 * tile + 3 * 4, max(len(paths), 1) * (tile + 4) + 4), 40, np.uint8)
    for j, p in enumerate(paths):
        ts = _ts(p)
        x, y = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
        f = read_frame(p)
        raw = _cut_report(f.data, x, y, 1)
        sig = np.repeat(np.repeat(A.load_sig(camera, p.name), 2, axis=0), 2, axis=1)
        sig = _cut_report(sig, x, y, 1)
        top = _stretch(raw, None)
        bot = _stretch(sig, (-2, 6))
        c0 = 4 + j * (tile + 4)
        sheet[4:4 + tile, c0:c0 + tile] = _ticks(_zoom(top))
        sheet[8 + tile:8 + 2 * tile, c0:c0 + tile] = _ticks(_zoom(bot))
    out.parent.mkdir(parents=True, exist_ok=True)
    write_png(out, sheet)
    out.with_suffix(".txt").write_text(
        f"track {track['id']} status {track['status']} velocity {track['velocity']}\n"
        + "\n".join(f"{h['frame_time']}  x={h['x']:.1f} y={h['y']:.1f}  sig={h['sig']:.1f}" for h in hits)
        + "\ncolumns: " + ", ".join(p.name[9:24] for p in paths) + "\n")
    return out, [p.name for p in paths]


def _ts(p: Path) -> float:
    from .fitsio import filename_time

    return filename_time(p.name).timestamp()


def _cut_report(a: np.ndarray, x: float, y: float, _unused) -> np.ndarray:
    """Cutout centred on report (x, y), returned in report orientation (row 0 = top)."""
    h, w = a.shape
    col, row = coords.report_to_fits(x, y, h)
    c, r = int(round(col)), int(round(row))
    out = np.full((2 * HALF + 1, 2 * HALF + 1), np.nan, np.float32)
    r0, r1, c0, c1 = r - HALF, r + HALF + 1, c - HALF, c + HALF + 1
    rr0, cc0 = max(r0, 0), max(c0, 0)
    rr1, cc1 = min(r1, h), min(c1, w)
    if rr0 < rr1 and cc0 < cc1:
        out[rr0 - r0:rr1 - r0, cc0 - c0:cc1 - c0] = a[rr0:rr1, cc0:cc1]
    return np.flipud(out)


def _stretch(a: np.ndarray, rng) -> np.ndarray:
    v = a[np.isfinite(a)]
    if v.size == 0:
        return np.zeros(a.shape, np.uint8)
    lo, hi = rng if rng else np.percentile(v, [1, 99.5])
    img = np.clip((np.nan_to_num(a, nan=lo) - lo) / max(hi - lo, 1e-30), 0, 1)
    return (img * 255).astype(np.uint8)


def _zoom(a: np.ndarray) -> np.ndarray:
    return np.repeat(np.repeat(a, SCALE, axis=0), SCALE, axis=1)


def _ticks(a: np.ndarray) -> np.ndarray:
    a = a.copy()
    c = a.shape[0] // 2
    L = 4 * SCALE
    a[:L, c - 1:c + 2] = 255
    a[-L:, c - 1:c + 2] = 255
    a[c - 1:c + 2, :L] = 255
    a[c - 1:c + 2, -L:] = 255
    return a
