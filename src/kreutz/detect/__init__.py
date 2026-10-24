"""Comet detector: run stages A (per frame), B (per window), C (tracking).

    from kreutz.detect import run
    tracks = run("ccor1", date(2025, 7, 17), date(2025, 7, 24))

Products live in data/derived/<camera>/<frames.VERSION>/:
  meta/<frame>.json     per-frame quality, star drift, Sun position
  sig/<frame>.npy       half-res significance map (float16)
  peaks/<frame>.json    stack peaks of the window ending at that frame
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
from .track import Track, Tracker
from .velocity import FRAME_SECONDS, VelocityGrid, from_answer_key

HISTORY = timedelta(hours=10)  # 16 background + 8 star frames + slack for gaps


def _workers(n: int | None, cap: int) -> int:
    return max(1, min(n or (os.cpu_count() or 2) - 2, cap))


def frame_list(camera: str, start: date, end: date) -> list[Path]:
    paths = fetch.local_frames(camera, start - timedelta(days=1), end)
    return [p for p in paths if A.is_standard(p)]


def _stage_a_chunk(args):
    camera, paths, lo, hi, dump = args
    A.process_range(camera, paths, range(lo, hi), dump_stages=set(dump), log=lambda *_: None)
    return hi - lo


def run_stage_a(camera: str, paths: list[Path], targets: list[int], workers=None, dump=()) -> None:
    todo = [i for i in targets if not A.meta_path(camera, paths[i].name).exists() or paths[i].name in dump]
    if not todo:
        return
    chunk = 48
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
    with ProcessPoolExecutor(_workers(workers, 6)) as ex:
        for n in ex.map(_stage_a_chunk, [(camera, paths, c[0], c[-1] + 1, list(dump)) for c in chunks]):
            done += n
            print(f"  {done}/{len(todo)} frames, {(_time.time() - t0) / 60:.1f} min", flush=True)


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


def peaks_path(camera: str, name: str) -> Path:
    return A.derived_dir(camera) / "peaks" / (Path(name).stem + ".json")


_GRID: VelocityGrid | None = None


def _init_grid(grid):
    global _GRID
    _GRID = grid


def _stage_b_window(args):
    camera, names, times = args
    maps = [A.load_sig(camera, n) for n in names]
    best, arg = B.stack_window(maps, times, _GRID)
    valid = np.isfinite(maps[-1])
    peaks = B.find_peaks(best, arg, valid, times[-1], _GRID, height=maps[-1].shape[0] * B.DS,
                         edge=B.edge_distance(maps[-1]), newest=maps[-1])
    out = peaks_path(camera, names[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([asdict(p) for p in peaks]))
    return len(peaks)


def run_stage_b(camera, paths, metas, windows, grid, workers=None) -> None:
    jobs = []
    for i in windows:
        mem = window_members(metas, i)
        if mem is None or peaks_path(camera, paths[i].name).exists():
            continue
        jobs.append((camera, [paths[j].name for j in mem], [metas[j].time for j in mem]))
    if not jobs:
        return
    print(f"stage B: {len(jobs)} windows x {len(grid)} velocities", flush=True)
    t0 = _time.time()
    with ProcessPoolExecutor(_workers(workers, 14), initializer=_init_grid, initargs=(grid,)) as ex:
        for k, _ in enumerate(ex.map(_stage_b_window, jobs, chunksize=4), 1):
            if k % 50 == 0 or k == len(jobs):
                print(f"  {k}/{len(jobs)} windows, {(_time.time() - t0) / 60:.1f} min", flush=True)


def run_stage_c(camera, paths, metas, windows) -> list[Track]:
    tr = Tracker()
    for i in windows:
        if window_members(metas, i) is None:
            continue
        p = peaks_path(camera, paths[i].name)
        if not p.exists():
            continue
        peaks = [B.Peak(**d) for d in json.loads(p.read_text())]
        mem = window_members(metas, i)
        drifts = [metas[j].drift for j in mem if metas[j].drift]
        drift = list(np.mean(drifts, axis=0)) if drifts else None
        tr.update(metas[i].time, peaks, drift, metas[i].sun_xy)
    return tr.finish()


def run(camera: str, start: date, end: date, workers=None, dump: tuple[str, ...] = ()) -> list[Track]:
    con = db.connect()
    grid = from_answer_key(con, camera)
    print("velocity grid:", grid.describe())
    paths = frame_list(camera, start, end)
    t_start = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    t_end = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    times = [datetime.strptime(p.name.split("_")[2], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc) for p in paths]
    targets = [i for i, t in enumerate(times) if t_start - HISTORY / 4 <= t < t_end]
    windows = [i for i, t in enumerate(times) if t_start <= t < t_end]
    run_stage_a(camera, paths, targets, workers, dump)
    metas = [A.load_meta(camera, p.name) for p in paths]
    run_stage_b(camera, paths, metas, windows, grid, workers)
    tracks = run_stage_c(camera, paths, metas, windows)
    out = A.derived_dir(camera) / f"tracks_{start}_{end}.json"
    out.write_text(json.dumps([track_dict(t, camera) for t in tracks], indent=1))
    good = [m for m in metas if m and m.ok]
    print(f"{len(good)} good frames, {sum(1 for m in good if m.star_frames < A.MIN_STAR_FRAMES)} without star model; "
          f"{len(tracks)} candidate tracks ({sum(1 for t in tracks if not t.rejected)} not rejected) -> {out}")
    return tracks


def track_dict(t: Track, camera: str) -> dict:
    vx, vy = t.velocity()
    return {
        "id": t.id, "camera": camera, "status": t.status, "merged": getattr(t, "merged", 0),
        "velocity": [vx, vy],
        "first_seen": _iso(t.hits[0].t),
        "reached": {str(k): _iso(v) for k, v in t.reached.items()},
        "star_drift": t.star_drift, "sun_xy": t.sun_xy,
        "hits": [{"frame_time": _iso(h.t), "x": h.x, "y": h.y, "sig": h.sig, "vx": h.vx, "vy": h.vy,
                  "edge": h.edge, "elong": h.elong} for h in t.hits],
    }


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def load_tracks(camera: str, start: date, end: date) -> list[dict]:
    p = A.derived_dir(camera) / f"tracks_{start}_{end}.json"
    return json.loads(p.read_text()) if p.exists() else []
