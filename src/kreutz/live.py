"""`kreutz watch`: keep the detector running on the real-time feed.

Each cycle (at most one per POLL_INTERVAL, the standing 15-minute limit):
  1. pull new frames from SWPC and new reports from Sungrazer
  2. stages A and B on whatever is new (both cache per frame)
  3. stage C over the last SPAN hours, from scratch (cheap: it only reads peaks)
  4. match the tracks against hunter reports, draw their images, and write
     data/live/<camera>/state.json for `kreutz serve`

Track ids are <first frame>_<x>_<y> of the first hit so they stay the same
from cycle to cycle, unlike the tracker's running counter.
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import cameras, contact, detect, fetch, net, paths, score
from .detect import envelope as E
from .detect import frames as A
from .detect.track import Track, Tracker
from .fitsio import filename_time
from .inspect import stretch, write_png
from .reports import db

SPAN_H = 48  # hours of windows tracked each cycle
SHOW_H = 24  # tracks whose last hit is older than this fall off the dashboard
FETCH_H = 36  # hours of live feed to fill on each poll (files on disk are skipped)
MAX_REJECTED = 60  # rejected tracks listed per cycle (no images)
PREVIEW_DS = 4  # full-frame preview downsample
LABELS = {"ccor1": "CCOR-1", "ccor2": "CCOR-2"}


def live_dir(camera: str) -> Path:
    return paths.DATA / "live" / camera


def track_key(t: dict) -> str:
    h = t["hits"][0]
    return f"{h['frame_time'][:16].replace('-', '').replace(':', '')}_{h['x']:.0f}_{h['y']:.0f}"


def watch(camera: str, once: bool = False, span_h: float = SPAN_H, fetch_h: float = FETCH_H) -> None:
    while True:
        t0 = time.time()
        try:
            cycle(camera, span_h=span_h, fetch_h=fetch_h)
        except (Exception, SystemExit):
            traceback.print_exc()
        if once:
            return
        # the fetch at the start of the cycle armed the 15-min poll guard
        time.sleep(max(net.POLL_INTERVAL_S - (time.time() - t0) + 5, 30))


def cycle(camera: str, span_h: float = SPAN_H, fetch_h: float = FETCH_H) -> dict:
    t0 = time.time()
    now = datetime.now(timezone.utc)
    counts = fetch.fetch_live(camera, hours=fetch_h)
    print(f"[{now:%H:%M:%S}] fetch: {counts['downloaded']} new frames", flush=True)
    _sync_reports(camera)
    con = db.connect()

    t_start = now - timedelta(hours=span_h)
    paths_ = detect.frame_list(camera, (t_start - detect.HISTORY).date(), now.date())
    times = [filename_time(p.name) for p in paths_]
    targets = [i for i, t in enumerate(times) if t >= t_start - detect.HISTORY]
    windows = [i for i, t in enumerate(times) if t >= t_start]
    months = sorted({times[i].month for i in windows}) or [now.month]
    envelopes = {m: E.for_month(con, camera, m) for m in months}
    detect.run_stage_a(camera, paths_, targets)
    metas = [A.load_meta(camera, p.name) for p in paths_]
    detect.run_stage_b(camera, paths_, metas, windows, envelopes)
    tracks, open_chains, last_window = _track(camera, paths_, metas, windows, envelopes)

    state = build_state(camera, [detect.track_dict(t, camera) for t in tracks], open_chains, metas, windows,
                        last_window, con, now)
    out = live_dir(camera)
    _images(camera, state, paths_, metas, out)
    state["cycle_s"] = round(time.time() - t0, 1)
    _write_json(out / "state.json", state)
    print(f"[{now:%H:%M:%S}] {camera}: {len(state['tracks'])} following, {len(state['rejected'])} rejected, "
          f"{state['open_chains']} open chains; cycle {state['cycle_s']} s -> {out / 'state.json'}", flush=True)
    return state


def _sync_reports(camera: str) -> None:
    from .reports import sync

    try:
        sync.sync([camera])
    except SystemExit:
        pass
    except Exception as e:  # the site being down must not stop the watcher
        print(f"reports sync failed: {e}", flush=True)


def _track(camera, paths_, metas, windows, envelopes):
    """Stage C, keeping the open (not yet candidate) chains for the status line."""
    tr = Tracker()
    last = None
    for i in windows:
        mem = detect.window_members(metas, i)
        if mem is None:
            continue
        env = envelopes[detect.month_of(metas[i].time)]
        p = detect.peaks_path(camera, paths_[i].name, env.grid)
        if not p.exists():
            continue
        peaks = [detect.B.Peak(**d) for d in json.loads(p.read_text())]
        drifts = [metas[j].drift for j in mem if metas[j].drift]
        drift = list(np.mean(drifts, axis=0)) if drifts else None
        seed = all(A.is_warm(metas[j]) for j in mem)
        tr.update(metas[i].time, peaks, drift, metas[i].sun_xy, envelope=env, seed=seed)
        last = metas[i]
    open_chains = [t for t in tr.active if len(t.hits) >= 2]
    return tr.finish(), open_chains, last


def build_state(camera: str, tracks: list[dict], open_chains: list[Track], metas, windows, last_window,
                con, now: datetime) -> dict:
    cutoff = (now - timedelta(hours=SHOW_H)).isoformat()
    recent = [t for t in tracks if t["hits"][-1]["frame_time"] >= cutoff]
    lat_s, lat_text = score.latency_s(camera)
    reports = _reports(con, camera, now)
    following, rejected = [], []
    for t in recent:
        d = _track_summary(t, reports, lat_s, con)
        (following if d["live"] else rejected).append(d)
    following.sort(key=lambda d: d["last_seen"], reverse=True)
    rejected.sort(key=lambda d: -d["best_sig"])
    good = [metas[i] for i in windows if metas[i] and metas[i].ok]
    longest = max(open_chains, key=lambda t: len(t.hits), default=None)
    return {
        "camera": camera, "label": LABELS.get(camera, camera),
        "updated": now.isoformat(timespec="seconds"),
        "window": _iso(last_window.time) if last_window else None,
        "latest_frame": last_window.name if last_window else None,
        "open_chains": len(open_chains),
        "longest": {"hits": len(longest.hits), "x": round(longest.last.x), "y": round(longest.last.y)} if longest else None,
        "good_frames": len(good), "cold_frames": sum(1 for m in good if not A.is_warm(m)),
        "latency_min": round(lat_s / 60, 1), "latency_text": lat_text,
        "tracks": following, "rejected": rejected[:MAX_REJECTED], "n_rejected": len(rejected),
        "sheet": {"half": contact.HALF, "scale": contact.SCALE, "gap": 4},
    }


def _reports(con, camera: str, now: datetime) -> list[score.Truth]:
    lo = (now - timedelta(hours=SPAN_H + 24)).date()
    return score.other_reports(con, camera, lo, now.date())


def _track_summary(t: dict, reports: list[score.Truth], lat_s: float, con) -> dict:
    hits = t["hits"]
    live = any(score.live_at(t, k) for k in score.THRESHOLDS) and not t["status"].startswith("rejected")
    vx, vy = t["velocity"]
    first, last = hits[0], hits[-1]
    span_frames = round((_ts(last["frame_time"]) - _ts(first["frame_time"])) / 900) + 1
    rep = next((r for r in reports if score.matches(t, r)), None)
    found = t["reached"].get("3") or first["frame_time"]
    out = {
        "id": track_key(t), "live": live, "status": t["status"], "status_at": t["status_at"],
        "reached": t["reached"], "merged": t["merged"],
        "n_hits": len(hits), "missing": max(span_frames - len(hits), 0),
        "first_seen": first["frame_time"], "last_seen": last["frame_time"],
        "x": round(last["x"]), "y": round(last["y"]),
        "velocity": [round(vx, 1), round(vy, 1)], "speed": round(float(np.hypot(vx, vy)), 1),
        "best_sig": round(max(h["sig"] for h in hits), 1),
        "hits": [{"t": h["frame_time"], "x": round(h["x"], 1), "y": round(h["y"], 1), "sig": round(h["sig"], 1)}
                 for h in hits],
        "report": None,
    }
    if rep is not None:
        rid = rep.report_ids[0]
        row = con.execute("SELECT reporter FROM reports WHERE report_id=?", (rid,)).fetchone()
        lead_h = (_ts(found) + lat_s - rep.first_post.timestamp()) / 3600
        out["report"] = {"id": rid, "reporter": row["reporter"] if row else rid, "status": rep.status,
                         "posted": rep.first_post.isoformat(timespec="minutes"),
                         "url": f"https://sungrazer.nrl.navy.mil/report/{rid.replace('_', '')}",
                         "lead_h": round(lead_h, 1)}
    return out


def _images(camera: str, state: dict, paths_, metas, out: Path) -> None:
    tdir = out / "tracks"
    tdir.mkdir(parents=True, exist_ok=True)
    for d in state["tracks"]:
        sheet = tdir / f"{d['id']}.png"
        info = sheet.with_suffix(".json")
        thumb = tdir / f"{d['id']}_thumb.png"
        track = {"camera": camera, "id": d["id"], "status": d["status"], "velocity": d["velocity"],
                 "hits": [{"frame_time": h["t"], "x": h["x"], "y": h["y"], "sig": h["sig"]} for h in d["hits"]]}
        meta = json.loads(info.read_text()) if info.exists() else {}
        if meta.get("n_hits") != d["n_hits"]:  # new or grew since it was drawn
            try:
                _, cols = contact.contact_sheet(track, sheet)
                _thumbnail(camera, d, thumb)
                meta = {"n_hits": d["n_hits"], "columns": cols}
                _write_json(info, meta)
            except Exception as e:
                print(f"images for {d['id']} failed: {e}", flush=True)
        d["sheet"] = f"{camera}/tracks/{sheet.name}" if sheet.exists() else None
        d["columns"] = [c[9:24] for c in meta.get("columns", [])]
        d["thumb"] = f"{camera}/tracks/{thumb.name}" if thumb.exists() else None
    _preview(camera, state, paths_, metas, out)
    keep = {p.name for d in state["tracks"] for p in (tdir / f"{d['id']}.png", tdir / f"{d['id']}.json",
                                                        tdir / f"{d['id']}_thumb.png")}
    for p in tdir.iterdir():
        if p.name not in keep:
            p.unlink()


def _thumbnail(camera: str, d: dict, out: Path) -> None:
    from .fitsio import read_frame
    from .frameindex import FrameIndex

    last = d["hits"][-1]
    idx = FrameIndex(camera)
    i = idx.nearest(datetime.fromisoformat(last["t"]))
    if i is None:
        return
    f = read_frame(idx.paths[i])
    raw = contact._cut_report(f.data, last["x"], last["y"], 1)
    write_png(out, contact._zoom(contact._stretch(raw, None)))


def _preview(camera: str, state: dict, paths_, metas, out: Path) -> None:
    from .fitsio import read_frame

    name = state.get("latest_frame")
    if not name:
        return
    png = out / "latest.png"
    if png.exists() and (out / "latest.json").exists() and json.loads((out / "latest.json").read_text()).get("frame") == name:
        return
    p = next((p for p in paths_ if p.name == name), None)
    if p is None:
        return
    f = read_frame(p)
    img = stretch(f.data)
    h, w = (img.shape[0] // PREVIEW_DS) * PREVIEW_DS, (img.shape[1] // PREVIEW_DS) * PREVIEW_DS
    small = img[:h, :w].reshape(h // PREVIEW_DS, PREVIEW_DS, w // PREVIEW_DS, PREVIEW_DS).mean(axis=(1, 3))
    write_png(png, small.astype(np.uint8))
    m = next((m for m in metas if m and m.name == name), None)
    _write_json(out / "latest.json", {"frame": name, "ds": PREVIEW_DS, "width": w // PREVIEW_DS,
                                      "height": h // PREVIEW_DS, "sun_xy": m.sun_xy if m else None})


def _write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=0))
    os.replace(tmp, path)


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def merged_state() -> dict:
    """Every camera's state.json, for /api/state."""
    cams = []
    for cam in cameras.CAMERAS:
        p = live_dir(cam) / "state.json"
        if p.exists():
            s = json.loads(p.read_text())
            lp = live_dir(cam) / "latest.json"
            s["latest"] = json.loads(lp.read_text()) if lp.exists() else None
            cams.append(s)
    return {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cameras": cams}
