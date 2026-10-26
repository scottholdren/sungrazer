"""Stage C: link stack peaks across consecutive windows into tracks.

Windows slide by one frame. A real object found in window w at position p with
velocity v should turn up in the next window near p + v*dt with about the same
velocity. A peak that does so in 3 consecutive windows becomes a candidate; we
keep following it (tolerating up to 2 missed windows) and record when it
reached 3, 4 and 5+ hits, and its status at that moment. No single
"confirmed" threshold is applied here.

Rejection is re-evaluated after every hit, and some rules use all hits so far
(glare-zone takes the median distance from the Sun), so a comet that was clean
at 3 hits can be rejected later as it nears the occulter. That later verdict
is kept as the track's final status, for the record, but it never rewrites
history: whether the track was submittable at k hits is status_at[k], and that
is what scoring uses.

One comet can come out as several tracks: a bright one's head and tail both
make peaks, tens of px apart along the direction of motion. Tracks with the
same velocity (within MATCH_DV) whose positions at common times are within
MERGE_ALONG px along the motion and MERGE_RADIUS across it are merged.

Rejection rules (see README), applied to candidates:
  stationary  speed below 1.0 px/frame
  star-drift  velocity within 1.5 px/frame of the measured star drift
  outbound    moving away from the Sun (cos of angle to Sun direction < 0)
  off-envelope  position angle or direction of motion outside where this
              month's Kreutz comets come from (envelope.py)
  glare-zone  median distance from the Sun < 320 px (occulter glare)
  edge        never more than 25 px from the occulter, arm or field/array edge
  extended    the blob under the hits is long and thin (median axis ratio > 3)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .stack import Peak
from .velocity import FRAME_SECONDS

VERSION = "c2"  # bump when linking or rejection rules change (the live watcher persists tracker state keyed on it)
MATCH_RADIUS = 6.0  # px, full resolution (half-res stacking + 0.5 px/frame velocity steps)
MATCH_DV = 2.0  # px/frame
MAX_MISSES = 2
CANDIDATE_HITS = 3
STAR_DV = 1.5
MIN_SPEED = 1.0
DEDUP_RADIUS = 12.0  # px: other peaks this close to a claimed one are the same object
MERGE_RADIUS = 8.0  # px: candidate tracks sharing >= 2 hit times this close are merged
MERGE_ALONG = 40.0  # px along the motion, for tracks with the same velocity (head and tail pieces)
SATELLITE_RADIUS = 60.0  # px: a shorter track this close to a longer one at >= 2 common times is a satellite of it
CONTINUE_FRAMES = 3  # a track starting within this many frames after another ends, on its path, continues it
GLARE_RADIUS = 320.0  # px; confirmed CCOR-1 comets were never reported closer than 392
EDGE_MIN = 25.0  # px
MAX_ELONGATION = 3.0


@dataclass
class Track:
    id: int
    hits: list[Peak] = field(default_factory=list)
    misses: int = 0
    consecutive: int = 0
    candidate: bool = False
    reached: dict = field(default_factory=dict)  # n hits -> unix time it happened
    status_at: dict = field(default_factory=dict)  # n hits -> status at that moment
    rejected: str | None = None
    star_drift: list | None = None
    sun_xy: list | None = None

    @property
    def last(self) -> Peak:
        return self.hits[-1]

    def velocity(self) -> tuple[float, float]:
        """Least-squares velocity over the hits (falls back to the stack grid value)."""
        if len(self.hits) < 3:
            return self.last.vx, self.last.vy
        t = np.array([h.t for h in self.hits]) / FRAME_SECONDS
        vx = np.polyfit(t, [h.x for h in self.hits], 1)[0]
        vy = np.polyfit(t, [h.y for h in self.hits], 1)[0]
        return float(vx), float(vy)

    def predict(self, t: float) -> tuple[float, float]:
        vx, vy = self.velocity()
        dt = (t - self.last.t) / FRAME_SECONDS
        return self.last.x + vx * dt, self.last.y + vy * dt

    @property
    def status(self) -> str:
        if self.rejected:
            return f"rejected:{self.rejected}"
        return "candidate" if self.candidate else "tentative"


def rejection(vx: float, vy: float, x: float, y: float, star_drift, sun_xy,
              hits: list[Peak] = (), envelope=None) -> str | None:
    speed = np.hypot(vx, vy)
    if speed < MIN_SPEED:
        return "stationary"
    if star_drift is not None and np.hypot(vx - star_drift[0], vy - star_drift[1]) < STAR_DV:
        return "star-drift"
    if sun_xy is not None:
        to_sun = np.array(sun_xy) - [x, y]
        if np.dot([vx, vy], to_sun) / (speed * np.linalg.norm(to_sun) + 1e-9) < 0:
            return "outbound"
    if envelope is not None:
        h0 = hits[0] if hits else None
        why = envelope.check(h0.x if h0 else x, h0.y if h0 else y, vx, vy, sun_xy)
        if why:
            return why
    if sun_xy is not None:
        r = np.median([np.hypot(h.x - sun_xy[0], h.y - sun_xy[1]) for h in hits] or [np.hypot(x - sun_xy[0], y - sun_xy[1])])
        if r < GLARE_RADIUS:
            return "glare-zone"
    edges = [h.edge for h in hits if h.edge is not None]
    if edges and max(edges) < EDGE_MIN:
        return "edge"
    el = [h.elong for h in hits if h.elong is not None]
    if el and np.median(el) > MAX_ELONGATION:
        return "extended"
    return None


class Tracker:
    def __init__(self):
        self.active: list[Track] = []
        self.done: list[Track] = []
        self._next = 0

    def update(self, t: float, peaks: list[Peak], star_drift, sun_xy, envelope=None, seed: bool = True) -> None:
        """Extend tracks with this window's peaks; if `seed`, unclaimed peaks start new tracks."""
        # Greedy matching, best-predicted first.
        claimed = set()
        pairs = []
        for ti, tr in enumerate(self.active):
            px, py = tr.predict(t)
            vx, vy = tr.velocity()
            for pi, pk in enumerate(peaks):
                d = np.hypot(pk.x - px, pk.y - py)
                if d <= MATCH_RADIUS and np.hypot(pk.vx - vx, pk.vy - vy) <= MATCH_DV:
                    pairs.append((d, -pk.sig, ti, pi))
        pairs.sort()
        matched_tracks = set()
        for _, _, ti, pi in pairs:
            if ti in matched_tracks or pi in claimed:
                continue
            matched_tracks.add(ti)
            claimed.add(pi)
            tr = self.active[ti]
            tr.hits.append(peaks[pi])
            tr.misses = 0
            tr.consecutive += 1
            n = len(tr.hits)
            if tr.consecutive >= CANDIDATE_HITS and not tr.candidate:
                tr.candidate = True
            if tr.candidate:
                tr.star_drift, tr.sun_xy = star_drift, sun_xy
                vx, vy = tr.velocity()
                tr.rejected = rejection(vx, vy, tr.last.x, tr.last.y, star_drift, sun_xy, tr.hits, envelope)
                for k in (3, 4, 5):
                    if n >= k and k not in tr.reached:
                        tr.reached[k] = t
                        tr.status_at[k] = tr.status
        still = []
        for ti, tr in enumerate(self.active):
            if ti not in matched_tracks:
                tr.misses += 1
                tr.consecutive = 0 if not tr.candidate else tr.consecutive
                if tr.misses > (MAX_MISSES if tr.candidate else 0):
                    if tr.candidate:
                        self.done.append(tr)
                    continue
            still.append(tr)
        # Unclaimed peaks start new tracks, except near a claimed or already-used
        # peak: one object gives several nearby peaks at neighbouring velocities.
        taken = [peaks[pi] for pi in claimed]
        if not seed:
            self.active = still
            return
        for pi in sorted(set(range(len(peaks))) - claimed, key=lambda i: -peaks[i].sig):
            pk = peaks[pi]
            if any(np.hypot(pk.x - q.x, pk.y - q.y) < DEDUP_RADIUS for q in taken):
                continue
            taken.append(pk)
            still.append(Track(self._next, [pk], consecutive=1))
            self._next += 1
        self.active = still

    def finish(self) -> list[Track]:
        self.done.extend(t for t in self.active if t.candidate)
        self.active = []
        return merge_duplicates(sorted(self.done, key=lambda t: t.hits[0].t))

    def snapshot(self, since: float = 0.0) -> list[Track]:
        """Merged candidates with a hit at or after `since`, leaving the tracker running
        (merging mutates, so copy)."""
        import copy

        cands = copy.deepcopy([t for t in self.done + self.active if t.candidate and t.last.t >= since])
        return merge_duplicates(sorted(cands, key=lambda t: t.hits[0].t))

    def prune(self, before: float) -> None:
        """Forget finished tracks whose last hit is older than `before`."""
        self.done = [t for t in self.done if t.last.t >= before]


def _same_object(a: Track, b: Track) -> bool:
    """Two tracks follow one object: >= 2 common hit times with the hits close
    (MERGE_RADIUS), or, at the same velocity, offset along the motion (MERGE_ALONG);
    or b is a satellite of a (>= 2 common times within SATELLITE_RADIUS: a bright
    comet's head, tail and neighbouring-velocity peaks); or b continues a: it
    starts within CONTINUE_FRAMES after a ends, on a's predicted path, at a's
    velocity. `a` is the longer track."""
    pos = {round(h.t): (h.x, h.y) for h in a.hits}
    common = [(np.subtract(pos[round(h.t)], (h.x, h.y))) for h in b.hits if round(h.t) in pos]
    if sum(np.hypot(*d) < MERGE_RADIUS for d in common) >= 2:
        return True
    if sum(np.hypot(*d) <= SATELLITE_RADIUS for d in common) >= 2:
        return True
    va, vb = np.array(a.velocity()), np.array(b.velocity())
    if np.hypot(*(va - vb)) > MATCH_DV:
        return False
    if not common:
        for first, second in ((a, b), (b, a)):
            gap = (second.hits[0].t - first.last.t) / FRAME_SECONDS
            if 0 < gap <= CONTINUE_FRAMES + 0.01:
                px, py = first.predict(second.hits[0].t)
                if np.hypot(second.hits[0].x - px, second.hits[0].y - py) <= SATELLITE_RADIUS:
                    return True
        return False
    u = (va + vb) / (np.linalg.norm(va + vb) + 1e-9)
    near = [abs(d @ u) <= MERGE_ALONG and abs(d[0] * u[1] - d[1] * u[0]) <= MERGE_RADIUS for d in common]
    return sum(near) >= 2


def _absorb(keep: Track, other: Track) -> None:
    """The merged track was submittable at k hits as soon as any piece was. A
    continuation extends the keeper's hits so later pieces can chain on."""
    keep.merged = getattr(keep, "merged", 0) + 1 + getattr(other, "merged", 0)
    if other.hits[0].t > keep.last.t:
        keep.hits.extend(other.hits)
    elif other.last.t < keep.hits[0].t:
        keep.hits[:0] = other.hits
    for k, t in other.reached.items():
        mine_ok = keep.status_at.get(k) == "candidate"
        theirs_ok = other.status_at.get(k) == "candidate"
        if k not in keep.reached or (theirs_ok and (not mine_ok or t < keep.reached[k])):
            keep.reached[k], keep.status_at[k] = t, other.status_at.get(k)


def merge_duplicates(tracks: list[Track]) -> list[Track]:
    """Fold candidate tracks that follow the same object into the longest one.
    Merging needs >= 2 common hit times, so only tracks overlapping in time are compared."""
    keep: list[Track] = []
    slack = CONTINUE_FRAMES * FRAME_SECONDS + 1
    for t in sorted(tracks, key=lambda t: -len(t.hits)):
        t0, t1 = t.hits[0].t, t.last.t
        dup = next((k for k in keep if k.hits[0].t <= t1 + slack and k.last.t >= t0 - slack and _same_object(k, t)),
                   None)
        if dup is None:
            t.merged = getattr(t, "merged", 0)
            keep.append(t)
        else:
            _absorb(dup, t)
    return sorted(keep, key=lambda t: t.hits[0].t)
