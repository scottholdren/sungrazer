"""Contact sheets: let a human vet a track by eye.

One column per frame, following the track's straight-line motion (fitted to its
hits) from the first frame of its first window to its last hit:
  top row     raw frame cutout (local stretch), report orientation (north up)
  middle row  stage-A significance map (-2 .. +6 sigma)
  bottom row  the detector's view: the last STACK_N maps shifted along the fitted
              path and summed (/ sqrt n), -2 .. +8 sigma
Small tick marks at the tile edges point at the predicted position. A real
comet is a dot that stays on the ticks and gets brighter, steadiest in the
bottom row; noise flickers, star residuals sit on a mask edge, and artefacts
line up with the occulter arm or the field edge.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import numpy as np

from . import coords
from .detect import frames as A
from .detect.velocity import FRAME_SECONDS
from .fitsio import read_frame, read_frame_cached
from .inspect import write_png

HALF = 24  # cutout half-size, full-res px
SCALE = 3
MAX_COLS = 20
ROWS = 3
STACK_N = 8  # maps summed for the bottom row, like stage B
STACK_SPAN_S = 14 * 900  # how far back those maps may reach


def contact_sheet(track: dict, out: Path, tiles: dict | None = None) -> tuple[Path, list[str]]:
    """Write the sheet; return its path and the frame name of each column.

    `tiles` (frame name -> (x, y, top, bottom)) caches drawn columns across calls
    for a growing track: a column is reused while the fitted path moves < 1.5 px."""
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

    d0 = datetime.fromtimestamp(t0 - STACK_SPAN_S).date()
    d1 = datetime.fromtimestamp(t[-1]).date()
    good = [p for p in frame_list(camera, d0, d1)
            if t0 - STACK_SPAN_S <= _ts(p) <= t[-1] + 60 and (m := A.load_meta(camera, p.name)) is not None and m.ok]
    paths = [p for p in good if _ts(p) >= t0]
    if len(paths) > MAX_COLS:
        paths = [paths[i] for i in np.linspace(0, len(paths) - 1, MAX_COLS).round().astype(int)]
    good_times = [_ts(p) for p in good]

    def stack_at(ts: float) -> np.ndarray:
        """The last STACK_N maps up to ts, each cut at the path's position at its own time."""
        acc, n = np.zeros((2 * HALF + 1,) * 2, np.float32), 0
        for p, tp in zip(reversed(good), reversed(good_times)):
            if tp > ts + 1 or tp < ts - STACK_SPAN_S:
                continue
            sig = np.repeat(np.repeat(A.load_sig(camera, p.name), 2, axis=0), 2, axis=1)
            acc += np.nan_to_num(_cut_report(sig, np.polyval(fx, tp / FRAME_SECONDS), np.polyval(fy, tp / FRAME_SECONDS), 1))
            n += 1
            if n >= STACK_N:
                break
        return acc / np.sqrt(max(n, 1))

    tile = (2 * HALF + 1) * SCALE
    sheet = np.full((ROWS * tile + (ROWS + 1) * 4, max(len(paths), 1) * (tile + 4) + 4), 40, np.uint8)
    for j, p in enumerate(paths):
        ts = _ts(p)
        x, y = np.polyval(fx, ts / FRAME_SECONDS), np.polyval(fy, ts / FRAME_SECONDS)
        hit = tiles.get(p.name) if tiles is not None else None
        if hit is not None and np.hypot(hit[0] - x, hit[1] - y) < 1.5 and len(hit) == 5:
            top, bot, stk = hit[2], hit[3], hit[4]
        else:
            f = read_frame_cached(p) if tiles is not None else read_frame(p)
            raw = _cut_report(f.data, x, y, 1)
            sig = np.repeat(np.repeat(A.load_sig(camera, p.name), 2, axis=0), 2, axis=1)
            sig = _cut_report(sig, x, y, 1)
            top = _stretch(raw, None)
            bot = _stretch(sig, (-2, 6))
            stk = _stretch(stack_at(ts), (-2, 8))
            if tiles is not None:
                tiles[p.name] = (x, y, top, bot, stk)
        c0 = 4 + j * (tile + 4)
        for r, img in enumerate((top, bot, stk)):
            r0 = 4 + r * (tile + 4)
            sheet[r0:r0 + tile, c0:c0 + tile] = _ticks(_zoom(img))
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
