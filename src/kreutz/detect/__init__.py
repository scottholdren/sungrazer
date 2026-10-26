"""Comet detector: run stages A (per frame), B (per window), C (tracking).

    from kreutz.detect import run
    tracks = run("ccor1", date(2025, 7, 17), date(2025, 7, 24))

Products live in data/derived/<camera>/<frames.VERSION>/:
  meta/<frame>.json     per-frame quality, star drift, Sun position
  sig/<frame>.npy       half-res significance map (float16)
  peaks/<grid>/<frame>.json  stack peaks of the window ending at that frame (per velocity grid)
  stages/<frame>/       every intermediate array, when asked for (--dump)
  tracks_<start>_<end>.json
"""

from __future__ import annotations

import json
import os
import time as _time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from .. import fetch
from ..reports import db
from . import frames as A
from . import stack as B
from . import envelope as E
from .track import Track, Tracker
from .velocity import FRAME_SECONDS, VelocityGrid

HISTORY = timedelta(hours=10)  # 16 background + 8 star frames + slack for gaps


def _workers(n: int | None, cap: int) -> int:
    # one core left for the fetcher/server; stage A needs ~0.8 GB per worker
    return max(1, min(n or (os.cpu_count() or 2) - 1, cap))


def frame_list(camera: str, start: date, end: date) -> list[Path]:
    paths = fetch.local_frames(camera, start - timedelta(days=1), end)
    return [p for p in paths if A.is_standard(p)]


def _stage_a_chunk(args):
    camera, paths, lo, hi, dump = args
    A.process_range(camera, paths, range(lo, hi), dump_stages=set(dump), log=lambda *_: None)
    return hi - lo


def run_stage_a(camera: str, paths: list[Path], targets: list[int], workers=None, dump=(), progress=None) -> None:
    """progress(done, total, names) is called before the work and after each chunk."""
    todo = [i for i in targets if not A.meta_path(camera, paths[i].name).exists() or paths[i].name in dump]
    if not todo:
        return
    if progress:
        progress(0, len(todo), [paths[i].name for i in todo])
    nw = _workers(workers, 6)
    # Each chunk re-reads its history (24 frames), so chunks should be long; but
    # split evenly across the workers so none sits idle at the end.
    chunk = max(8, min(48, -(-len(todo) // nw)))
    # contiguous chunks of the todo list
    chunks, cur = [], [todo[0]]
    for i in todo[1:]:
        if i == cur[-1] + 1 and len(cur) < chunk:
            cur.append(i)
        else:
            chunks.append(cur)
            cur = [i]
    chunks.append(cur)
    t0, done = _time.time(), 0
    print(f"stage A: {len(todo)} frames in {len(chunks)} chunks", flush=True)
    with ProcessPoolExecutor(nw) as ex:
        for n in ex.map(_stage_a_chunk, [(camera, paths, c[0], c[-1] + 1, list(dump)) for c in chunks]):
            done += n
            print(f"  {done}/{len(todo)} frames, {(_time.time() - t0) / 60:.1f} min", flush=True)
            if progress:
                progress(done, len(todo), [paths[i].name for i in todo])


def window_members(metas: list[A.FrameMeta | None], i: int) -> list[int] | None:
    """Indices of the N good frames ending at i, or None if the window is unusable."""
    if metas[i] is None or not metas[i].ok:
        return None
    members = [i]
    j = i - 1
    while len(members) < B.N_FRAMES and j >= 0:
        if metas[j] is not None and metas[j].ok:
            members.append(j)
        j -= 1
    members.reverse()
    span = (metas[members[-1]].time - metas[members[0]].time) / FRAME_SECONDS
    if len(members) < B.MIN_FRAMES or span > B.MAX_SPAN_FRAMES:
        return None
    return members


def peaks_path(camera: str, name: str, grid: VelocityGrid) -> Path:
    return A.derived_dir(camera) / "peaks" / f"{B.VERSION}-{grid.key()}" / (Path(name).stem + ".json")


def month_of(ts: float) -> int:
    return datetime.fromtimestamp(ts, timezone.utc).month


_GRIDS: dict[int, VelocityGrid] = {}


def _init_grids(grids):
    global _GRIDS
    _GRIDS = grids


def _stage_b_window(args):
    camera, names, times, month = args
    grid = _GRIDS[month]
    maps = [A.load_sig(camera, n) for n in names]
    best, arg = B.stack_window(maps, times, grid)
    valid = np.isfinite(maps[-1])
    peaks = B.find_peaks(best, arg, valid, times[-1], grid, height=maps[-1].shape[0] * B.DS,
                         edge=B.edge_distance(maps[-1]), cutouts=B.Cutouts(maps, times, grid))
    out = peaks_path(camera, names[-1], grid)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([asdict(p) for p in peaks]))
    return len(peaks)


def run_stage_b(camera, paths, metas, windows, envelopes, workers=None, progress=None) -> None:
    """progress(done, total, names) is called before the work and after each window."""
    jobs = []
    for i in windows:
        mem = window_members(metas, i)
        if mem is None:
            continue
        month = month_of(metas[i].time)
        if peaks_path(camera, paths[i].name, envelopes[month].grid).exists():
            continue
        jobs.append((camera, [paths[j].name for j in mem], [metas[j].time for j in mem], month))
    if not jobs:
        return
    grids = {m: e.grid for m, e in envelopes.items()}
    names = [j[1][-1] for j in jobs]
    print(f"stage B: {len(jobs)} windows x {'/'.join(str(len(g)) for g in grids.values())} velocities", flush=True)
    if progress:
        progress(0, len(jobs), names)
    t0 = _time.time()
    with ProcessPoolExecutor(_workers(workers, 14), initializer=_init_grids, initargs=(grids,)) as ex:
        for k, _ in enumerate(ex.map(_stage_b_window, jobs, chunksize=4), 1):
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} windows, {(_time.time() - t0) / 60:.1f} min", flush=True)
            if progress:
                progress(k, len(jobs), names)


# Start tracks from windows containing cold frames (frames.py: warm-up after a gap). The gate
# was added for slow artefacts after the daily gaps; the per-month grid no longer stacks such
# velocities, and on Oct 17-24 2026 seeding cold windows found Chris Wage's Oct 23 comet 3.25 h
# earlier (real lead -2.9 h instead of +0.4 h) for 19% more live tracks.
SEED_COLD = True


def run_stage_c(camera, paths, metas, windows, envelopes, seed_cold: bool = SEED_COLD) -> list[Track]:
    tr = Tracker()
    cold = 0
    for i in windows:
        mem = window_members(metas, i)
        if mem is None:
            continue
        env = envelopes[month_of(metas[i].time)]
        p = peaks_path(camera, paths[i].name, env.grid)
        if not p.exists():
            continue
        peaks = [B.Peak(**d) for d in json.loads(p.read_text())]
        drifts = [metas[j].drift for j in mem if metas[j].drift]
        drift = list(np.mean(drifts, axis=0)) if drifts else None
        warm = all(A.is_warm(metas[j]) for j in mem)
        cold += not warm
        tr.update(metas[i].time, peaks, drift, metas[i].sun_xy, envelope=env, seed=warm or seed_cold)
    print(f"stage C: {cold} windows with cold frames " + ("could start tracks (seed_cold)" if seed_cold
                                                         else "started no new tracks"))
    return tr.finish()


def run(camera: str, start: date, end: date, workers=None, dump: tuple[str, ...] = (),
        seed_cold: bool = SEED_COLD) -> list[Track]:
    con = db.connect()
    paths = frame_list(camera, start, end)
    t_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    t_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    times = [datetime.strptime(p.name.split("_")[2], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) for p in paths]
    targets = [i for i, t in enumerate(times) if t_start - HISTORY / 4 <= t < t_end]
    windows = [i for i, t in enumerate(times) if t_start <= t < t_end]
    envelopes = {m: E.for_month(con, camera, m) for m in sorted({times[i].month for i in windows})}
    for e in envelopes.values():
        print("envelope", e.describe())
    run_stage_a(camera, paths, targets, workers, dump)
    metas = [A.load_meta(camera, p.name) for p in paths]
    run_stage_b(camera, paths, metas, windows, envelopes, workers)
    tracks = run_stage_c(camera, paths, metas, windows, envelopes, seed_cold=seed_cold)
    out = A.derived_dir(camera) / f"tracks_{start}_{end}.json"
    out.write_text(json.dumps([track_dict(t, camera) for t in tracks], indent=1))
    good = [m for m in metas if m and m.ok]
    print(f"{len(good)} good frames, {sum(1 for m in good if m.star_frames < A.MIN_STAR_FRAMES)} without star model, "
          f"{sum(1 for m in good if not A.is_warm(m))} cold; "
          f"{len(tracks)} candidate tracks ({sum(1 for t in tracks if not t.rejected)} not rejected) -> {out}")
    return tracks


def track_dict(t: Track, camera: str) -> dict:
    vx, vy = t.velocity()
    return {
        "id": t.id, "camera": camera, "status": t.status, "merged": getattr(t, "merged", 0),
        "velocity": [vx, vy],
        "first_seen": _iso(t.hits[0].t),
        "reached": {str(k): _iso(v) for k, v in t.reached.items()},
        "status_at": {str(k): v for k, v in t.status_at.items()},
        "star_drift": t.star_drift, "sun_xy": t.sun_xy,
        "hits": [{"frame_time": _iso(h.t), "x": h.x, "y": h.y, "sig": h.sig, "vx": h.vx, "vy": h.vy,
                  "edge": h.edge, "elong": h.elong} for h in t.hits],
    }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def load_tracks(camera: str, start: date, end: date) -> list[dict]:
    p = A.derived_dir(camera) / f"tracks_{start}_{end}.json"
    return json.loads(p.read_text()) if p.exists() else []
