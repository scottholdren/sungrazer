"""Obvious movers in any direction: comets that are not Kreutz.

The main search (stages B and C) only looks along the Kreutz lane: it stacks
frames at the velocities Kreutz comets have this month and rejects whatever
travels another way (`off-envelope`, `outbound`). Meyer, Kracht, Marsden and
non-group comets, about one CCOR-1 report in ten, come from elsewhere, many
of them slow or moving away from the Sun in the image.

This does not search for those in general. It follows the ones that need no
search: objects bright enough to stand out of a single frame's significance
map, linked from frame to frame at a constant velocity in whatever direction.
Of the eight confirmed non-Kreutz comets of 2025-08-21..30, two were like
that (7-9 sigma per frame: the Meyer of 08-28, the Kracht II of 08-30); the
other six sat at 3-6 sigma and would need stacking along their own motion.

Per frame: the local maxima of the half-res map at MOVER_PEAK sigma or more.
A chain starts from two peaks in consecutive frames MIN_SPEED..MAX_SPEED
apart, and is extended by the peak nearest its predicted place. A chain of
MOVER_HITS hits whose median is MOVER_SIGMA or more, and which passes the
artefact rules of the main tracker (not star drift, not in the glare, not on
the arm, not on an edge, not elongated), is a mover.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from . import frames as A
from . import stack as B
from . import track as C
from .velocity import FRAME_SECONDS

VERSION = "m2"
MOVER_PEAK = 6.0     # sigma of a single-frame map maximum worth linking
MAX_PEAKS = 200      # per frame, brightest first, after the glare, the arm and the edges are left out
MIN_SPEED = 2.0      # px/frame, as the main tracker: slower is a detector defect or a star residual
MAX_SPEED = 16.0     # px/frame: the fastest reported comet did 14
LINK_RADIUS = 4.0    # px: a peak this close to the predicted place extends the chain (peaks sit on a 2 px grid)
MAX_GAP = 1          # frames a chain may miss in a row
MOVER_HITS = 6       # hits that make a chain a mover (1.5 h; chance and CME debris gave chains of 4 and 5) ...
MOVER_SIGMA = 7.0    # ... if their median is this bright
SATURATED = 90.0     # a median at the map's ceiling (100) is the Moon's rim or a planet, not a comet (2025-07-23)


@dataclass
class P:
    t: float
    x: float
    y: float
    sig: float
    edge: float | None = None
    elong: float | None = None


def frame_peaks(sig: np.ndarray, t: float, sun_xy=None, scale: float = 1.0) -> list[P]:
    """The single-frame peaks of a half-res map, brightest first, in full-res report px.
    Peaks in the glare, on the arm or on an edge are left out here: they are most of a
    frame's maxima and would crowd the rest out of MAX_PEAKS."""
    b = np.where(np.isfinite(sig), sig, -np.inf)
    local = (b == ndimage.maximum_filter(b, size=5)) & (b >= MOVER_PEAK)
    rows, cols = np.nonzero(local)
    edge = B.edge_distance(sig)
    out = []
    for k in np.argsort(b[rows, cols])[::-1]:
        r, c = int(rows[k]), int(cols[k])
        x, y = B.coords_from_ds(c, r, sig.shape[0] * B.DS)
        e = float(edge[r, c] * B.DS)
        if e < C.EDGE_MIN:
            continue
        if sun_xy is not None and (np.hypot(x - sun_xy[0], y - sun_xy[1]) < C.GLARE_RADIUS * scale or abs(x - sun_xy[0]) < C.PYLON_HALF * scale):
            continue
        out.append(P(t, float(x), float(y), float(b[r, c]), e, float(B.elongation(sig, r, c))))
        if len(out) >= MAX_PEAKS:
            break
    return out


def peaks_path(camera: str, name: str) -> Path:
    return A.derived_dir(camera) / "movers" / f"{VERSION}-{Path(name).stem}.json"


def load_peaks(camera: str, name: str, t: float, sun_xy=None, scale: float = 1.0) -> list[P]:
    """Cached per frame: the map does not change once written."""
    p = peaks_path(camera, name)
    if p.exists():
        return [P(**d) for d in json.loads(p.read_text())]
    peaks = frame_peaks(A.load_sig(camera, name), t, sun_xy, scale)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps([asdict(q) for q in peaks]))
    return peaks


@dataclass
class Chain:
    hits: list[P]
    misses: int = 0

    def velocity(self) -> tuple[float, float]:
        t = np.array([h.t for h in self.hits]) / FRAME_SECONDS
        if len(self.hits) == 2:
            dt = t[1] - t[0]
            return (self.hits[1].x - self.hits[0].x) / dt, (self.hits[1].y - self.hits[0].y) / dt
        return float(np.polyfit(t, [h.x for h in self.hits], 1)[0]), float(np.polyfit(t, [h.y for h in self.hits], 1)[0])

    def predict(self, t: float) -> tuple[float, float]:
        vx, vy = self.velocity()
        dt = (t - self.hits[-1].t) / FRAME_SECONDS
        return self.hits[-1].x + vx * dt, self.hits[-1].y + vy * dt


def link(frames: list[tuple[float, list[P]]]) -> list[Chain]:
    """Chains of MOVER_HITS or more hits over (time, peaks) frames in time order."""
    open_: list[Chain] = []
    done: list[Chain] = []
    prev: tuple[float, list[P]] | None = None
    for t, peaks in frames:
        used: set[int] = set()
        xy = np.array([[p.x, p.y] for p in peaks], float).reshape(-1, 2)
        still = []
        for ch in sorted(open_, key=lambda c: -len(c.hits)):  # the longest chain takes its peak first
            k = None
            if len(xy):
                px, py = ch.predict(t)
                d = np.hypot(xy[:, 0] - px, xy[:, 1] - py)
                d[list(used)] = np.inf
                k = int(np.argmin(d)) if d.min() <= LINK_RADIUS else None
            if k is None:
                ch.misses += 1
                if ch.misses > (MAX_GAP if len(ch.hits) >= 3 else 0):
                    if len(ch.hits) >= MOVER_HITS:
                        done.append(ch)
                    continue
            else:
                used.add(k)
                ch.hits.append(peaks[k])
                ch.misses = 0
            still.append(ch)
        open_ = still
        if prev is not None and len(xy) and 0 < (dt := (t - prev[0]) / FRAME_SECONDS) <= 1 + MAX_GAP + 0.01:
            tails = {(c.hits[-1].t, c.hits[-1].x, c.hits[-1].y) for c in open_} | {(c.hits[-2].t, c.hits[-2].x, c.hits[-2].y) for c in open_ if len(c.hits) >= 2}
            for a in prev[1]:
                if (a.t, a.x, a.y) in tails:
                    continue
                d = np.hypot(xy[:, 0] - a.x, xy[:, 1] - a.y) / dt
                for k in np.nonzero((d >= MIN_SPEED) & (d <= MAX_SPEED))[0]:
                    if int(k) not in used:
                        open_.append(Chain([a, peaks[int(k)]]))
        prev = (t, peaks)
    return done + [c for c in open_ if len(c.hits) >= MOVER_HITS]


def artefact(ch: Chain, star_drift, sun_xy, scale: float = 1.0) -> str | None:
    """Why a chain is not an object, by the main tracker's rules that do not concern its direction."""
    vx, vy = ch.velocity()
    if np.median([h.sig for h in ch.hits]) >= SATURATED:
        return "saturated"
    if np.hypot(vx, vy) < MIN_SPEED * scale:
        return "stationary"
    if star_drift is not None and np.hypot(vx - star_drift[0], vy - star_drift[1]) < C.STAR_DV * scale:
        return "star-drift"
    if sun_xy is not None:
        if np.median([np.hypot(h.x - sun_xy[0], h.y - sun_xy[1]) for h in ch.hits]) < C.GLARE_RADIUS * scale:
            return "glare-zone"
        if np.median([abs(h.x - sun_xy[0]) for h in ch.hits]) < C.PYLON_HALF * scale:
            return "pylon"
    if max(h.edge for h in ch.hits if h.edge is not None) < C.EDGE_MIN:
        return "edge"
    if np.median([h.elong for h in ch.hits if h.elong is not None]) > C.MAX_ELONGATION:
        return "extended"
    return None


def dedupe(chains: list[Chain]) -> list[Chain]:
    """One chain per object: of chains within 12 px of each other at two common times
    (a bright object gives several maxima), the longest."""
    keep: list[Chain] = []
    for ch in sorted(chains, key=lambda c: -len(c.hits)):
        mine = {h.t: (h.x, h.y) for h in ch.hits}
        if not any(sum(h.t in mine and np.hypot(h.x - mine[h.t][0], h.y - mine[h.t][1]) < 12 for h in k.hits) >= 2 for k in keep):
            keep.append(ch)
    return keep


def movers(frames: list[tuple[float, list[P]]], star_drift, sun_xy, scale: float = 1.0) -> list[Chain]:
    """The obvious movers of a run of frames: bright, steady, and no artefact."""
    out = [ch for ch in dedupe(link(frames))
           if np.median([h.sig for h in ch.hits]) >= MOVER_SIGMA and artefact(ch, star_drift, sun_xy, scale) is None]
    return sorted(out, key=lambda c: c.hits[0].t)
