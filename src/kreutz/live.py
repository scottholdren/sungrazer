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
import pickle
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import cameras, contact, detect, fetch, net, paths, score
from .detect import envelope as E
from .detect import frames as A
from .detect import stack as B
from .detect import track as C
from .detect.track import Track, Tracker
from .fitsio import filename_time
from .inspect import stretch, write_png
from .reports import db

SPAN_H = 48  # hours of windows tracked each cycle
SHOW_H = 24  # tracks whose last hit is older than this fall off the dashboard
FETCH_H = 36  # hours of live feed to fill on each poll (files on disk are skipped)
SYNC_DAYS = 60  # reports listing depth after the first (full) sync of a run
MAX_REJECTED = 60  # rejected tracks listed per cycle (no images)
NEWS_H = 3  # "new object" events only for tracks that began within this many hours
PREVIEW_DS = 4  # full-frame preview downsample
LABELS = {"ccor1": "CCOR-1", "ccor2": "CCOR-2"}


def live_dir(camera: str) -> Path:
    return paths.DATA / "live" / camera


def track_key(t: dict) -> str:
    h = t["hits"][0]
    return f"{h['frame_time'][:16].replace('-', '').replace(':', '')}_{h['x']:.0f}_{h['y']:.0f}"


class Activity:
    """What the watcher is doing right now and what it did lately, for the dashboard.

    data/live/<camera>/activity.json = {"current": {...}, "events": [...]}; rewritten
    on every change so the page can poll it every few seconds."""

    MAX_EVENTS = 300

    def __init__(self, camera: str):
        self.path = live_dir(camera) / "activity.json"
        self.events: list[dict] = []
        self.current: dict | None = None
        if self.path.exists():
            try:
                self.events = json.loads(self.path.read_text()).get("events", [])
            except Exception:
                pass

    def now(self, stage: str, detail: str = "", done: int | None = None, total: int | None = None,
            names: list[str] | None = None) -> None:
        keep_since = self.current and self.current.get("stage") == stage
        self.current = {"stage": stage, "detail": detail, "done": done, "total": total,
                        "names": (names or [])[:12],
                        "since": self.current["since"] if keep_since else _now_iso()}
        self._write()

    def event(self, msg: str, kind: str = "info", **extra) -> None:
        self.events.append({"t": _now_iso(), "kind": kind, "msg": msg, **extra})
        self.events = self.events[-self.MAX_EVENTS:]
        print(f"  * {msg}", flush=True)
        self._write()

    def _write(self) -> None:
        _write_json(self.path, {"current": self.current, "events": self.events})


class Watcher:
    """State kept between cycles: the tracker, drawn sheet tiles, sync depth."""

    def __init__(self, camera: str, span_h: float = SPAN_H, fetch_h: float = FETCH_H):
        self.camera, self.span_h, self.fetch_h = camera, span_h, fetch_h
        self.tracker: Tracker | None = None
        self.tracker_key = None
        self.fed_until = 0.0  # window time last given to the tracker
        self.tiles: dict[str, dict] = {}  # track id -> contact-sheet column cache
        self.activity = Activity(camera)
        self.known: dict[str, dict] | None = None  # track id -> summary from the previous cycle
        self.stage_a: A.StageA | None = None  # resident stage A, primed after the first backlog run
        self.snapshot: list[Track] | None = None  # merged candidates as of the last window fed

    def run(self, once: bool = False) -> None:
        if not once and net.last_poll(f"live:{self.camera}") + net.POLL_INTERVAL_S > time.time():
            wait = seconds_to_next_poll(self.camera)  # a restart within the guard: wait for the slot
            at = datetime.now(timezone.utc) + timedelta(seconds=wait)
            print(f"restarted inside the poll guard; first poll at {at:%H:%M:%S} UTC", flush=True)
            self.activity.now("idle", f"restarted; first poll at {at:%H:%M:%S} UTC")
            time.sleep(wait)
        while True:
            try:
                self.cycle()
            except (Exception, SystemExit) as e:
                traceback.print_exc()
                self.activity.event(f"cycle failed: {type(e).__name__}: {e}", kind="error")
            if once:
                return
            wait = seconds_to_next_poll(self.camera)
            at = datetime.now(timezone.utc) + timedelta(seconds=wait)
            print(f"next poll in {wait / 60:.1f} min", flush=True)
            self.activity.now("idle", f"next poll at {at:%H:%M:%S} UTC")
            time.sleep(wait)

    def cycle(self) -> dict:
        camera, timings, act = self.camera, {}, self.activity
        t0 = time.time()
        now = datetime.now(timezone.utc)

        act.now("fetch", "polling the SWPC listing")
        counts = fetch.fetch_live(camera, hours=self.fetch_h)
        timings["fetch"] = _lap(t0)
        print(f"[{now:%H:%M:%S}] fetch: {counts['downloaded']} new frames", flush=True)
        if counts.get("skipped"):
            act.event("fetch skipped: polled less than 15 min ago", kind="warn")
        else:
            act.event(f"fetched {_n(counts['downloaded'], 'new frame')}" + (": " + ", ".join(
                n[9:24] for n in counts["new"]) if counts["new"] else ""), kind="fetch")
        full = now.timestamp() - _last_full_sync() > 86400
        since = date(2025, 1, 1) if full else now.date() - timedelta(days=SYNC_DAYS)
        act.now("reports", f"syncing Sungrazer reports since {since}" + (" (daily full sync)" if full else ""))
        if _sync_reports(camera, since) and full:
            _write_json(_full_sync_file(), {"at": now.isoformat(timespec="seconds")})
        timings["reports"] = _lap(t0, timings)
        con = db.connect()
        newest = con.execute("SELECT reporter, report_timestamp FROM reports WHERE camera=? "
                             "ORDER BY report_timestamp DESC LIMIT 1", (camera,)).fetchone()
        if newest:
            act.event(f"reports synced; newest {LABELS.get(camera, camera)} report by {newest[0]} "
                      f"at {newest[1][5:16].replace('T', ' ')} UTC", kind="reports")

        t_start = now - timedelta(hours=self.span_h)
        paths_ = detect.frame_list(camera, (t_start - detect.HISTORY).date(), now.date())
        times = [filename_time(p.name) for p in paths_]
        targets = [i for i, t in enumerate(times) if t >= t_start - detect.HISTORY]
        windows = [i for i, t in enumerate(times) if t >= t_start]
        months = sorted({times[i].month for i in windows}) or [now.month]
        envelopes = {m: E.for_month(con, camera, m) for m in months}

        def prog_a(done, total, names):
            act.now("stage A", f"background, stars and significance: {done}/{_n(total, 'frame')}", done, total, names)
            if done == 0:
                act.event(f"stage A: {_n(total, 'frame')} to process" + (": " + ", ".join(n[9:24] for n in names[:4])
                                                                     + (" ..." if total > 4 else "")), kind="stage")
        act.now("stage A", "checking which frames need processing")
        self._stage_a(paths_, targets, prog_a)
        timings["stage_a"] = _lap(t0, timings)
        metas = [A.load_meta(camera, p.name) for p in paths_]

        def prog_b(done, total, names):
            act.now("stage B", f"shift-and-stack over {sum(len(e.grid) for e in envelopes.values())} velocities: "
                    f"{done}/{_n(total, 'window')}", done, total, names)
            if done == 0:
                act.event(f"stage B: {_n(total, 'window')} to stack, newest ending {names[-1][9:24]}", kind="stage")
        act.now("stage B", "checking which windows need stacking")
        detect.run_stage_b(camera, paths_, metas, windows, envelopes, progress=prog_b)
        timings["stage_b"] = _lap(t0, timings)
        act.now("stage C", "linking peaks into tracks")
        tracks, open_chains, last_window = self._track(paths_, metas, windows, envelopes, t_start)
        timings["stage_c"] = _lap(t0, timings)

        act.now("state", "matching tracks against hunter reports")
        state = build_state(camera, [detect.track_dict(t, camera) for t in tracks], open_chains, metas, windows,
                            last_window, con, now)
        timings["state"] = _lap(t0, timings)
        self._announce(state)
        out = live_dir(camera)
        act.now("images", f"drawing sheets for tracks that grew")
        n_drawn = _images(camera, state, paths_, metas, out, self.tiles)
        timings["images"] = _lap(t0, timings)
        state["cycle_s"] = round(time.time() - t0, 1)
        state["timings"] = timings
        state["poll"] = poll_phase(camera)
        _write_json(out / "state.json", state)
        act.event(f"cycle done in {state['cycle_s']} s: {len(state['tracks'])} following, "
                  f"{state['n_rejected']} rejected, {_n(state['open_chains'], 'open chain')}, {_n(n_drawn, 'sheet')} drawn; "
                  + ", ".join(f"{k} {v}s" for k, v in timings.items()), kind="cycle")
        print(f"[{now:%H:%M:%S}] {camera}: {len(state['tracks'])} following, {len(state['rejected'])} rejected, "
              f"{state['open_chains']} open chains; cycle {state['cycle_s']} s: "
              + ", ".join(f"{k} {v}" for k, v in timings.items()), flush=True)
        return state

    def _announce(self, state: dict) -> None:
        """Events for tracks that appeared, grew into something, or matched a report since last cycle."""
        cur = {d["id"]: d for d in state["tracks"]}
        if self.known is None:
            p = live_dir(self.camera) / "state.json"
            prev = json.loads(p.read_text())["tracks"] if p.exists() else []
            self.known = {d["id"]: d for d in prev}
        for tid, d in cur.items():
            old = self.known.get(tid)
            where = f"at {d['x']},{d['y']}, {d['speed']} px/frame, {d['n_hits']} hits, {d['best_sig']}σ"
            if old is None:
                if d.get("group", tid) != tid and d["group"] in self.known:
                    self.activity.event(f"track {tid} joins object {d['group']} {where}", kind="info", track=d["group"])
                    continue
                if time.time() - _ts(d["first_seen"]) > NEWS_H * 3600:
                    continue  # an old track resurfacing (regrouped, or its head aged out) is not news
                self.activity.event(f"new object {tid} {where}" + (" — UNREPORTED" if not d["report"] else ""),
                                    kind="track", track=tid)
            elif d["report"] and not old.get("report"):
                r = d["report"]
                self.activity.event(f"track {tid} matches a report by {r['reporter']} ({r['lead_h']:+.1f} h)",
                                    kind="track", track=tid)
            elif d["n_hits"] >= 5 and old["n_hits"] < 5:
                self.activity.event(f"track {tid} reached 5 hits {where}", kind="track", track=tid)
        self.known = cur

    def _stage_a(self, paths_, targets, progress) -> None:
        """Feed new frames to the resident StageA when they directly follow what it
        has seen; otherwise (first cycle, restart, a frame that arrived out of
        order) run the batch stage over the backlog and re-prime from the cache."""
        todo = [i for i in targets if not A.meta_path(self.camera, paths_[i].name).exists()]
        if not todo:
            return
        names = [paths_[i].name for i in todo]
        st = self.stage_a
        contiguous = todo == list(range(todo[0], targets[-1] + 1)) and todo[0] > 0
        if st is not None and contiguous and st.last_name == paths_[todo[0] - 1].name:
            progress(0, len(todo), names)
            for k, i in enumerate(todo, 1):
                st.feed(paths_[i], wanted=True)
                progress(k, len(todo), names)
            if st.withdrawn:
                self.activity.event(f"withdrew {', '.join(n[9:24] for n in st.withdrawn)}: next frame had the Earth in view",
                                    kind="warn")
                st.withdrawn = []
            return
        detect.run_stage_a(self.camera, paths_, targets, progress=progress)
        self.activity.now("stage A", f"priming the resident stage from the last {A.HISTORY_FRAMES} frames")
        t0 = time.time()
        self.stage_a = A.prime(self.camera, paths_, targets[-1], log=lambda *_: None)
        self.activity.event(f"stage A primed in {time.time() - t0:.0f} s; new frames are processed in-process from now on",
                            kind="stage")

    def _track(self, paths_, metas, windows, envelopes, t_start: datetime):
        """Stage C. The tracker persists between cycles and across restarts (tracker.pkl),
        so normally only the newest window is fed; any change to the rules, the
        velocity grid or the span rebuilds it from scratch."""
        key = (C.VERSION, B.VERSION, tuple(sorted((m, e.grid.key()) for m, e in envelopes.items())), self.span_h,
               detect.SEED_COLD)
        pkl = live_dir(self.camera) / "tracker.pkl"
        if self.tracker is None and pkl.exists():
            try:
                saved = pickle.loads(pkl.read_bytes())
                if saved["key"] == key:
                    self.tracker, self.tracker_key, self.fed_until = saved["tracker"], key, saved["fed_until"]
            except Exception as e:
                print(f"tracker.pkl unreadable, rebuilding: {e}", flush=True)
        if self.tracker is None or self.tracker_key != key:
            self.tracker, self.tracker_key, self.fed_until, self.snapshot = Tracker(), key, 0.0, None
        tr, last, fed = self.tracker, None, 0
        for i in windows:
            if metas[i] is None or not metas[i].ok:
                continue
            last = metas[i]
            if metas[i].time <= self.fed_until:
                continue
            mem = detect.window_members(metas, i)
            if mem is None:
                continue
            env = envelopes[detect.month_of(metas[i].time)]
            p = detect.peaks_path(self.camera, paths_[i].name, env.grid)
            if not p.exists():
                continue
            peaks = [B.Peak(**d) for d in json.loads(p.read_text())]
            drifts = [metas[j].drift for j in mem if metas[j].drift]
            drift = list(np.mean(drifts, axis=0)) if drifts else None
            seed = detect.SEED_COLD or all(A.is_warm(metas[j]) for j in mem)
            tr.update(metas[i].time, peaks, drift, metas[i].sun_xy, envelope=env, seed=seed)
            self.fed_until = metas[i].time
            fed += 1
        open_chains = [t for t in tr.active if len(t.hits) >= 2]
        print(f"stage C: {_n(fed, 'new window')} fed; {len(tr.active)} active, {len(tr.done)} finished", flush=True)
        if fed or self.snapshot is None:
            tr.prune(t_start.timestamp())
            _write_bytes(pkl, pickle.dumps({"key": key, "tracker": tr, "fed_until": self.fed_until}))
            shown_since = (datetime.now(timezone.utc) - timedelta(hours=SHOW_H)).timestamp()
            self.snapshot = tr.snapshot(since=shown_since)  # merging is the cost; nothing changed otherwise
        return self.snapshot, open_chains, last


def watch(camera: str, once: bool = False, span_h: float = SPAN_H, fetch_h: float = FETCH_H) -> None:
    import faulthandler
    import signal

    faulthandler.register(signal.SIGUSR1, all_threads=True)  # `kill -USR1 <pid>` dumps every stack to stderr
    Watcher(camera, span_h, fetch_h).run(once=once)


def cycle(camera: str, span_h: float = SPAN_H, fetch_h: float = FETCH_H) -> dict:
    return Watcher(camera, span_h, fetch_h).cycle()


def _lap(t0: float, timings: dict | None = None) -> float:
    return round(time.time() - t0 - sum(timings.values() if timings else ()), 1)


def poll_phase(camera: str) -> dict:
    """When in each 15-minute slot to poll, from the publication log: the minute
    after the slot's frame that minimises the expected wait between a file
    appearing on SWPC and our next poll (files missed wait a whole interval)."""
    lag = fetch.publication_lags(camera)
    if lag is None or len(lag) < 50:
        return {"phase_min": None, "expected_wait_min": net.POLL_INTERVAL_S / 120, "basis": "none"}
    lag = np.asarray(lag)
    best = None
    for phase in np.arange(0, 15, 0.5):
        wait = (phase - lag) % 15  # minutes from publication to the next poll at this phase
        exp = float(wait.mean())
        if best is None or exp < best[1]:
            best = (float(phase), exp)
    return {"phase_min": best[0], "expected_wait_min": round(best[1], 1), "basis": f"{len(lag)} files"}


def seconds_to_next_poll(camera: str) -> float:
    """Sleep until the next slot's poll time, never under the 15-minute guard."""
    now = datetime.now(timezone.utc)
    phase = poll_phase(camera)["phase_min"]
    earliest = net.last_poll(f"live:{camera}") + net.POLL_INTERVAL_S
    if phase is None:
        return max(earliest - now.timestamp(), 30)
    # frames start at HH:00:25, :15:25, ...; the poll goes `phase` minutes after that
    slot = now.replace(minute=(now.minute // 15) * 15, second=25, microsecond=0)
    for k in range(0, 4):
        t = slot + timedelta(minutes=15 * k + phase)
        # a slot a few seconds inside the guard (the last poll ran seconds after its
        # slot) is still this slot: wait for the guard, not for the next slot
        if t.timestamp() >= earliest - 60 and t > now:
            return max(t.timestamp(), earliest) - now.timestamp() + 1
    return max(earliest - now.timestamp(), 30)


def _full_sync_file() -> Path:
    return paths.STATE_DIR / "reports_full_sync.json"


def _last_full_sync() -> float:
    p = _full_sync_file()
    try:
        return datetime.fromisoformat(json.loads(p.read_text())["at"]).timestamp()
    except Exception:
        return 0.0


def _sync_reports(camera: str, since: date) -> bool:
    """Sync this camera's reports plus LASCO C2/C3 (matched by time only), so
    "unreported" means unreported on any Sungrazer camera."""
    from .reports import sync

    # its guard was armed a few seconds after the fetch's last cycle; don't lose a whole cycle over that
    remaining = net.last_poll("sungrazer") + net.POLL_INTERVAL_S - time.time()
    if 0 < remaining <= 60:
        time.sleep(remaining + 0.5)
    try:
        sync.sync([camera, "c2", "c3"], since=since)
        return True
    except SystemExit:
        pass
    except Exception as e:  # the site being down must not stop the watcher
        print(f"reports sync failed: {e}", flush=True)
    return False


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
    group_objects(following)
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


OBJECT_PX = 60.0  # tracks this close at the same time (or on each other's path) are one object
CONTINUE_FRAMES = 3  # a track starting within this many frames after another ends, on its path, continues it


def _same_object(a: dict, b: dict) -> bool:
    pos = {h["t"]: (h["x"], h["y"]) for h in a["hits"]}
    for h in b["hits"]:
        p = pos.get(h["t"])
        if p is not None and np.hypot(h["x"] - p[0], h["y"] - p[1]) <= OBJECT_PX:
            return True
    for first, second in ((a, b), (b, a)):
        gap = (_ts(second["first_seen"]) - _ts(first["last_seen"])) / 900.0
        if 0 < gap <= CONTINUE_FRAMES + 0.01:
            vx, vy = first["velocity"]
            px, py = first["x"] + vx * gap, first["y"] + vy * gap
            h0 = second["hits"][0]
            if np.hypot(h0["x"] - px, h0["y"] - py) <= OBJECT_PX:
                return True
    return False


def group_objects(tracks: list[dict]) -> None:
    """Label each track with the object it belongs to: `group` is the id of the
    longest track in the cluster, and that track lists the others as `pieces`.
    Presentation only: the tracker's own merge is narrower on purpose."""
    parent = list(range(len(tracks)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            if find(i) != find(j) and _same_object(tracks[i], tracks[j]):
                parent[find(j)] = find(i)
    clusters: dict[int, list[dict]] = {}
    for i, t in enumerate(tracks):
        clusters.setdefault(find(i), []).append(t)
    for members in clusters.values():
        head = max(members, key=lambda t: (t["n_hits"], t["best_sig"]))
        for t in members:
            t["group"] = head["id"]
            t["pieces"] = []
        head["pieces"] = [t["id"] for t in members if t is not head]


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
    out["lasco"] = lasco_report(con, first["frame_time"], last["frame_time"], found, lat_s)
    return out


LASCO_SLACK_H = 1.0  # CCOR-1's field lies inside C3's, so a comet seen in both is seen at the same time


def lasco_report(con, first: str, last: str, found: str, lat_s: float) -> dict | None:
    """The earliest LASCO C2/C3 claim whose frame times overlap ours. Positions
    can't be compared across instruments, so this is only a hint, shown as such."""
    lo = (datetime.fromisoformat(first) - timedelta(hours=LASCO_SLACK_H)).isoformat()
    hi = (datetime.fromisoformat(last) + timedelta(hours=LASCO_SLACK_H)).isoformat()
    row = con.execute("""SELECT r.report_id, r.reporter, r.camera, r.status, r.report_timestamp
                         FROM reports r JOIN frames f USING (report_id)
                         WHERE r.camera IN ('c2', 'c3') AND r.sighting_type = 'Potential Comet'
                           AND r.status != 'retracted' AND f.frame_time BETWEEN ? AND ?
                         ORDER BY r.report_timestamp LIMIT 1""", (lo, hi)).fetchone()
    if row is None:
        return None
    posted = datetime.fromisoformat(row["report_timestamp"])
    return {"id": row["report_id"], "reporter": row["reporter"], "status": row["status"],
            "posted": posted.isoformat(timespec="minutes"),
            "url": f"https://sungrazer.nrl.navy.mil/report/{row['report_id'].replace('_', '')}",
            "lead_h": round((_ts(found) + lat_s - posted.timestamp()) / 3600, 1),
            "camera": row["camera"].upper()}


def _images(camera: str, state: dict, paths_, metas, out: Path, tiles: dict | None = None) -> int:
    """Draw/refresh each followed track's sheet and thumbnail; returns how many were drawn."""
    tdir = out / "tracks"
    tdir.mkdir(parents=True, exist_ok=True)
    if tiles is not None:
        for k in list(tiles):
            if not any(d["id"] == k for d in state["tracks"]):
                del tiles[k]
    drawn = 0
    for d in state["tracks"]:
        sheet = tdir / f"{d['id']}.png"
        info = sheet.with_suffix(".json")
        thumb = tdir / f"{d['id']}_thumb.png"
        track = {"camera": camera, "id": d["id"], "status": d["status"], "velocity": d["velocity"],
                 "hits": [{"frame_time": h["t"], "x": h["x"], "y": h["y"], "sig": h["sig"]} for h in d["hits"]]}
        meta = json.loads(info.read_text()) if info.exists() else {}
        if meta.get("n_hits") != d["n_hits"]:  # new or grew since it was drawn
            try:
                cache = tiles.setdefault(d["id"], {}) if tiles is not None else None
                _, cols = contact.contact_sheet(track, sheet, tiles=cache)
                _thumbnail(camera, d, thumb)
                meta = {"n_hits": d["n_hits"], "columns": cols}
                _write_json(info, meta)
                drawn += 1
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
    return drawn


def _thumbnail(camera: str, d: dict, out: Path) -> None:
    from .fitsio import read_frame_cached
    from .frameindex import FrameIndex

    last = d["hits"][-1]
    idx = FrameIndex(camera, datetime.fromisoformat(last["t"]).date() - timedelta(days=1))
    i = idx.nearest(datetime.fromisoformat(last["t"]))
    if i is None:
        return
    f = read_frame_cached(idx.paths[i])
    raw = contact._cut_report(f.data, last["x"], last["y"], 1)
    write_png(out, contact._zoom(contact._stretch(raw, None)))


def _preview(camera: str, state: dict, paths_, metas, out: Path) -> None:
    from .fitsio import read_frame_cached

    name = state.get("latest_frame")
    if not name:
        return
    png = out / "latest.png"
    if png.exists() and (out / "latest.json").exists() and json.loads((out / "latest.json").read_text()).get("frame") == name:
        return
    p = next((p for p in paths_ if p.name == name), None)
    if p is None:
        return
    f = read_frame_cached(p)
    d = f.data
    h, w = (d.shape[0] // PREVIEW_DS) * PREVIEW_DS, (d.shape[1] // PREVIEW_DS) * PREVIEW_DS
    with np.errstate(all="ignore"):
        small = np.nanmean(d[:h, :w].reshape(h // PREVIEW_DS, PREVIEW_DS, w // PREVIEW_DS, PREVIEW_DS), axis=(1, 3))
    write_png(png, stretch(small))  # stretching the small image: the radial profile loop is 16x cheaper
    m = next((m for m in metas if m and m.name == name), None)
    _write_json(out / "latest.json", {"frame": name, "ds": PREVIEW_DS, "width": w // PREVIEW_DS,
                                      "height": h // PREVIEW_DS, "sun_xy": m.sun_xy if m else None})


def _write_json(path: Path, obj) -> None:
    _write_bytes(path, json.dumps(obj, indent=0).encode())


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def _n(k: int, word: str) -> str:
    return f"{k} {word}{'' if k == 1 else 's'}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def merged_activity() -> dict:
    """Every camera's activity.json, for /api/activity."""
    out = {"now": _now_iso(), "cameras": {}}
    for cam in cameras.CAMERAS:
        p = live_dir(cam) / "activity.json"
        if p.exists():
            try:
                out["cameras"][cam] = json.loads(p.read_text())
            except Exception:
                pass
    return out


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
