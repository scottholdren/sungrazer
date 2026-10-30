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
# Alert tiers, from the operating-point tables of 2026-10-26 (results/STATUS.md): (min hits, min best sigma).
# alert: Oct 17-24 precision 0.60, 3/5 comets, median real lead -3.6 h; watch: July 7/8 comets at ~+0.2 h.
TIERS = {"alert": (4, 15.0), "watch": (5, 10.0)}
DASHBOARD_URL = os.environ.get("KREUTZ_DASHBOARD_URL")  # appended to Discord posts when set
PREVIEW_DS = 4  # full-frame preview downsample
LABELS = {"ccor1": "CCOR-1", "ccor2": "CCOR-2"}


def live_dir(camera: str) -> Path:
    return paths.DATA / "live" / camera


def track_key(t: dict) -> str:
    h = t["hits"][0]
    return f"{h['frame_time'][:16].replace('-', '').replace(':', '')}_{h['x']:.0f}_{h['y']:.0f}"


class Activity:
    """What the watcher is doing right now and what it did lately, for the dashboard.

    data/live/<camera>/activity.json = {"current": {...}, "events": [...], "changes": [...]};
    rewritten on every change so the page can poll it every few seconds. `events` is
    everything the watcher did; `changes` is only what happened to candidates (new,
    grew, tiered, reported, merged), each tied to a track for the detail view."""

    MAX_EVENTS = 300
    MAX_CHANGES = 500

    def __init__(self, camera: str):
        self.path = live_dir(camera) / "activity.json"
        self.events: list[dict] = []
        self.changes: list[dict] = []
        self.current: dict | None = None
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text())
                self.events = saved.get("events", [])
                self.changes = saved.get("changes", [])
            except Exception:
                pass

    def now(self, stage: str, detail: str = "", done: int | None = None, total: int | None = None,
            names: list[str] | None = None, until: datetime | None = None) -> None:
        """`until`: when this stage ends by itself (an idle wait); the page shows it in local time."""
        keep_since = self.current and self.current.get("stage") == stage
        self.current = {"stage": stage, "detail": detail, "done": done, "total": total,
                        "names": (names or [])[:12], "until": until.isoformat() if until else None,
                        "since": self.current["since"] if keep_since else _now_iso()}
        self._write()

    def event(self, msg: str, kind: str = "info", **extra) -> None:
        self.events.append({"t": _now_iso(), "kind": kind, "msg": msg, **extra})
        self.events = self.events[-self.MAX_EVENTS:]
        print(f"  * {msg}", flush=True)
        self._write()

    def change(self, msg: str, kind: str, track: str, **extra) -> None:
        """A candidate changed: kind in new / grew / watch / alert / report / merge."""
        self.changes.append({"t": _now_iso(), "kind": kind, "msg": msg, "track": track, **extra})
        self.changes = self.changes[-self.MAX_CHANGES:]
        self._write()

    def _write(self) -> None:
        _write_json(self.path, {"current": self.current, "events": self.events, "changes": self.changes})


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
        self.sparks: list[dict] = []  # bright two-hit chains as of the last window fed
        self.phase = poll_phase(camera)["phase_min"]  # fixed for the life of the process: no slot flapping
        self.pending_posts: list[str] = []  # alert-tier track ids to post to Discord once their sheet exists

    def run(self, once: bool = False) -> None:
        if not once and net.last_poll(f"live:{self.camera}") + net.POLL_INTERVAL_S > time.time():
            wait = seconds_to_next_poll(self.camera, self.phase)  # a restart within the guard: wait for the slot
            at = datetime.now(timezone.utc) + timedelta(seconds=wait)
            print(f"restarted inside the poll guard; first poll at {at:%H:%M:%S} UTC", flush=True)
            self.activity.now("idle", "restarted; first poll", until=at)
            time.sleep(wait)
        while True:
            try:
                self.cycle()
            except (Exception, SystemExit) as e:
                traceback.print_exc()
                self.activity.event(f"cycle failed: {type(e).__name__}: {e}", kind="error")
            if once:
                return
            wait = seconds_to_next_poll(self.camera, self.phase)
            at = datetime.now(timezone.utc) + timedelta(seconds=wait)
            print(f"next poll in {wait / 60:.1f} min", flush=True)
            self.activity.now("idle", "next poll", until=at)
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
        for name in counts.get("new", []):  # say why a frame was thrown out, or the dashboard just looks stuck
            m = next((m for m in metas if m is not None and m.name == name), None)
            if m is not None and not m.ok:
                act.event(f"frame {name[9:24]} unusable: {'; '.join(m.problems) or 'stage A rejected it'}"
                          " — no new window until good frames return", kind="warn")

        def prog_b(done, total, names):
            act.now("stage B", f"shift-and-stack over {sum(len(e.grid) for e in envelopes.values())} velocities: "
                    f"{done}/{_n(total, 'window')}", done, total, names)
            if done == 0:
                act.event(f"stage B: {_n(total, 'window')} to stack, newest ending {names[-1][9:24]}", kind="stage")
        act.now("stage B", "checking which windows need stacking")
        detect.run_stage_b(camera, paths_, metas, windows, envelopes, progress=prog_b)
        timings["stage_b"] = _lap(t0, timings)
        act.now("stage C", "linking peaks into tracks")
        tracks, open_chains, last_window, sparks_ = self._track(paths_, metas, windows, envelopes, t_start)
        timings["stage_c"] = _lap(t0, timings)

        act.now("state", "matching tracks against hunter reports")
        state = build_state(camera, [detect.track_dict(t, camera) for t in tracks], open_chains, metas, windows,
                            last_window, con, now, sparks=sparks_)
        timings["state"] = _lap(t0, timings)
        self._announce(state)
        out = live_dir(camera)
        act.now("images", f"drawing sheets for tracks that grew")
        n_drawn = _images(camera, state, paths_, metas, out, self.tiles)
        timings["images"] = _lap(t0, timings)
        self._post_alerts(state, out)
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

    def _post_alerts(self, state: dict, out: Path) -> None:
        """Discord posts for the alert-tier objects announced this cycle (after their sheets are drawn)."""
        from . import notify

        if not self.pending_posts:
            return
        ids, self.pending_posts = self.pending_posts, []
        if not notify.webhook_url():
            return
        by_id = {d["id"]: d for d in state["tracks"]}
        for tid in ids:
            d = by_id.get(tid)
            if d is None:
                continue
            try:
                if notify.post_alert(d, state["label"], out / "tracks" / f"{tid}.png", DASHBOARD_URL):
                    self.activity.event(f"posted {tid} to Discord", kind="info", track=tid)
            except Exception as e:
                self.activity.event(f"Discord post for {tid} failed: {e}", kind="error", track=tid)

    def _announce(self, state: dict) -> None:
        """Events for tracks that appeared, grew into something, or matched a report since last cycle."""
        cur = {d["id"]: d for d in state["tracks"]}
        labels = load_labels(self.camera)
        for tid, d in cur.items():
            rec = labels.get(tid)
            if rec and rec["label"] == "noise" and not rec.get("contradicted"):
                why = ("reached the alert tier" if d["tier"] == "alert"
                       else f"matches a report by {d['report']['reporter']}" if d["report"] else None)
                if why:
                    contradict_label(self.camera, tid, why)
                    self.activity.event(f"{tid} was labelled noise but {why} — label contradicted, still tracked",
                                        kind="warn", track=tid)
        if self.known is None:
            p = live_dir(self.camera) / "state.json"
            prev = json.loads(p.read_text())["tracks"] if p.exists() else []
            self.known = {d["id"]: d for d in prev}
        for tid, d in cur.items():
            old = self.known.get(tid)
            where = f"at {d['x']},{d['y']}, {d['speed']} px/frame, {d['n_hits']} hits, {d['best_sig']}σ"
            unrep = " — UNREPORTED" if not d["report"] else ""
            if old is None:
                if d.get("group", tid) != tid and d["group"] in self.known:
                    self.activity.event(f"track {tid} joins object {d['group']} {where}", kind="info", track=d["group"])
                    self.activity.change(f"track {tid} joins object {d['group']} {where}", "merge", d["group"])
                    continue
                if not _recent(d["first_seen"]):
                    continue  # an old track resurfacing (regrouped, or its head aged out) is not news
                self.activity.event(f"new object {tid} {where}{unrep}", kind="track", track=tid)
                self.activity.change(f"new {tid} {where}{unrep}", "new", tid)
            elif d["report"] and not old.get("report"):
                r = d["report"]
                msg = f"{tid} matches a report by {r['reporter']} ({r['lead_h']:+.1f} h)"
                self.activity.event(f"track {msg}", kind="track", track=tid)
                self.activity.change(msg, "report", tid)
            elif d["tier"] == "alert" and old.get("tier") != "alert" and _recent(d["tier_at"]):
                self.activity.event(f"ALERT {tid} {where}{unrep}", kind="alert", track=tid)
                self.activity.change(f"ALERT {tid} {where}{unrep}", "alert", tid)
                self.pending_posts.append(tid)
            elif d["tier"] == "watch" and old.get("tier") is None and _recent(d["tier_at"]):
                self.activity.event(f"watch {tid} {where}{unrep}", kind="track", track=tid)
                self.activity.change(f"watch {tid} {where}{unrep}", "watch", tid)
            elif d["n_hits"] > old["n_hits"] or d["best_sig"] >= old["best_sig"] + 1.0:
                self.activity.change(f"{tid} grew to {d['n_hits']} hits, {d['best_sig']}σ "
                                     f"(was {old['n_hits']} hits, {old['best_sig']}σ){unrep}", "grew", tid)
        for tid, old in self.known.items():  # file every candidate the tracker is done with, for training
            if tid not in cur and not old.get("held"):
                _archive(self.camera, old)
        self.known = cur
        seen = getattr(self, "known_sparks", set())
        for s in state.get("sparks", []):
            if s["id"] not in seen and s["id"] not in cur:
                sigs = ", ".join(f"{h['sig']}σ" for h in s["hits"])
                self.activity.change(f"spark {s['id']} at {s['x']},{s['y']}, {s['speed']} px/frame, 2 hits: {sigs}", "spark", s["id"])
        self.known_sparks = {s["id"] for s in state.get("sparks", [])}

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
        tr, last, fed, scene = self.tracker, None, 0, None

        def scene_of(i):  # what the rejection rules need for window i
            mem = detect.window_members(metas, i)
            env = envelopes[detect.month_of(metas[i].time)]
            drifts = [metas[j].drift for j in mem if metas[j].drift] if mem else []
            return mem, list(np.mean(drifts, axis=0)) if drifts else None, metas[i].sun_xy, env

        for i in windows:
            if metas[i] is None or not metas[i].ok:
                continue
            last = metas[i]
            if metas[i].time <= self.fed_until:
                continue
            mem, drift, sun_xy, env = scene_of(i)
            if mem is None:
                continue
            p = detect.peaks_path(self.camera, paths_[i].name, env.grid)
            if not p.exists():
                continue
            peaks = [B.Peak(**d) for d in json.loads(p.read_text())]
            seed = detect.SEED_COLD or all(A.is_warm(metas[j]) for j in mem)
            tr.update(metas[i].time, peaks, drift, sun_xy, envelope=env, seed=seed)
            self.fed_until = metas[i].time
            scene = (drift, sun_xy, env)
            fed += 1
        open_chains = [t for t in tr.active if len(t.hits) >= 2]
        print(f"stage C: {_n(fed, 'new window')} fed; {len(tr.active)} active, {len(tr.done)} finished", flush=True)
        if fed or self.snapshot is None:
            tr.prune(t_start.timestamp())
            _write_bytes(pkl, pickle.dumps({"key": key, "tracker": tr, "fed_until": self.fed_until}))
            shown_since = (datetime.now(timezone.utc) - timedelta(hours=SHOW_H)).timestamp()
            self.snapshot = tr.snapshot(since=shown_since)  # merging is the cost; nothing changed otherwise
            if scene is None:  # restarted with nothing new: judge the chains by the window they last hit on
                fed_i = next((i for i in windows if metas[i] and metas[i].time == self.fed_until), None)
                scene = scene_of(fed_i)[1:] if fed_i is not None else None
            self.sparks = sparks(tr, *scene) if scene else []
        return self.snapshot, open_chains, last, self.sparks


SPARK_SIGMA = 10.0  # a two-hit chain this bright on both hits (the watch tier's brightness) is a spark


def sparks(tr: Tracker, drift, sun_xy, envelope) -> list[dict]:
    """Two-hit chains worth a look before their third frame: both hits at least
    SPARK_SIGMA and nothing the candidate rules would reject. Open chains are
    dropped on their first miss, so these all hit on the newest window; the
    id is the one the track will carry if it becomes a candidate."""
    out = []
    for t in tr.active:
        if len(t.hits) != 2 or t.candidate or min(h.sig for h in t.hits) < SPARK_SIGMA:
            continue
        vx, vy = t.velocity()
        if C.rejection(vx, vy, t.last.x, t.last.y, drift, sun_xy, t.hits, envelope):
            continue
        hits = [{"t": detect._iso(h.t), "x": round(h.x, 1), "y": round(h.y, 1), "sig": round(h.sig, 1)} for h in t.hits]
        out.append({"id": track_key({"hits": [{"frame_time": hits[0]["t"], "x": t.hits[0].x, "y": t.hits[0].y}]}),
                    "hits": hits, "first_seen": hits[0]["t"], "last_seen": hits[-1]["t"],
                    "x": round(t.last.x), "y": round(t.last.y),
                    "velocity": [round(vx, 1), round(vy, 1)], "speed": round(float(np.hypot(vx, vy)), 1),
                    "best_sig": round(max(h.sig for h in t.hits), 1), "min_sig": round(min(h.sig for h in t.hits), 1)})
    out.sort(key=lambda d: -d["min_sig"])
    return out


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


SLOT_S = 15 * 60
SLOT_OFFSET_S = 25  # frames start at HH:00:25, :15:25, ...


def seconds_to_next_poll(camera: str, phase: float | None) -> float:
    """Seconds until the poll of the slot after the last poll's slot, `phase` minutes
    past the frame time, never inside the 15-minute guard. Never skips a slot: if
    the guard reaches past the slot's poll time we wait for the guard instead."""
    now = time.time()
    last = net.last_poll(f"live:{camera}")
    earliest = last + net.POLL_INTERVAL_S
    if phase is None:
        return max(earliest - now, 30)
    slot_of = lambda t: (t - SLOT_OFFSET_S) // SLOT_S  # noqa: E731
    k = slot_of(last) + 1 if last else slot_of(now)
    t = k * SLOT_S + SLOT_OFFSET_S + phase * 60
    while t <= now:  # the slot after the last poll is already gone (we were down): the next one
        k += 1
        t = k * SLOT_S + SLOT_OFFSET_S + phase * 60
    return max(t, earliest) - now + 1


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
                con, now: datetime, sparks: list[dict] = ()) -> dict:
    cutoff = (now - timedelta(hours=SHOW_H)).isoformat()
    held = {k for k, rec in load_labels(camera).items() if rec.get("label") == "comet"}
    recent = [t for t in tracks if t["hits"][-1]["frame_time"] >= cutoff or track_key(t) in held]
    lat_s, lat_text = score.latency_s(camera)
    reports = _reports(con, camera, now)
    following, rejected = [], []
    for t in recent:
        d = _track_summary(t, reports, lat_s, con)
        if d["id"] in held and not (d["live"] and d["last_seen"] >= cutoff):
            d["held"] = True  # only the owner's comet label keeps it here
        (following if d["live"] or d["id"] in held else rejected).append(d)
    _hold(camera, following, held)
    rank = {"alert": 0, "watch": 1, None: 2}
    following.sort(key=lambda d: (rank[d["tier"]], d["last_seen"]), reverse=False)
    following.sort(key=lambda d: rank[d["tier"]])  # stable: alerts first, then watch, each newest-first
    rejected.sort(key=lambda d: -d["best_sig"])
    group_objects(following)
    # a spark on top of a followed object (a bright comet's satellite peaks) is that object, not news
    taken = [(h["t"], h["x"], h["y"]) for d in following for h in d["hits"]]
    sparks = [s for s in sparks if not any(t == h["t"] and np.hypot(h["x"] - x, h["y"] - y) < OBJECT_PX
                                          for h in s["hits"] for t, x, y in taken)]
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
        "sparks": list(sparks),
        "sheet": {"half": contact.HALF, "scale": contact.SCALE, "gap": 4},
    }


def _archive(camera: str, d: dict) -> None:
    """data/live/<camera>/archive/YYYY-MM/<id>.json: the last summary of a candidate
    that has left the dashboard. `kreutz learn` labels these from the reports."""
    month = d["first_seen"][:7]
    _write_json(live_dir(camera) / "archive" / month / f"{d['id']}.json",
                {k: v for k, v in d.items() if k not in ("group", "pieces", "sheet", "thumb", "columns", "label")})


def _hold(camera: str, following: list[dict], held: set[str]) -> None:
    """The owner's comet labels keep a track on the dashboard until the label is
    cleared: a copy of each labelled track's last summary is kept, and once the
    tracker has let the track go (aged out, pruned) the copy stands in for it."""
    hdir = live_dir(camera) / "held"
    have = {d["id"] for d in following}
    for k in held - have:
        p = hdir / f"{k}.json"
        if p.exists():
            d = json.loads(p.read_text())
            d["held"] = True
            following.append(d)
    for d in following:
        if d["id"] in held:
            _write_json(hdir / f"{d['id']}.json", {k: v for k, v in d.items() if k not in ("group", "pieces")})


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
    tier, tier_at = None, None
    for name, (k, s) in TIERS.items():
        at = _fires_at(hits, k, s)
        if at is not None:
            tier, tier_at = name, at
            break
    out = {
        "tier": tier, "tier_at": tier_at,
        "id": track_key(t), "live": live, "status": t["status"], "status_at": t["status_at"],
        "reached": t["reached"], "merged": t["merged"],
        "n_hits": len(hits), "missing": max(span_frames - len(hits), 0),
        "first_seen": first["frame_time"], "last_seen": last["frame_time"],
        "x": round(last["x"]), "y": round(last["y"]),
        "velocity": [round(vx, 1), round(vy, 1)], "speed": round(float(np.hypot(vx, vy)), 1),
        "best_sig": round(max(h["sig"] for h in hits), 1), "sun_xy": t.get("sun_xy"),
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


def _recent(iso: str | None) -> bool:
    return iso is not None and time.time() - _ts(iso) <= NEWS_H * 3600


def _fires_at(hits: list[dict], k: int, s: float) -> str | None:
    """Frame time at which the track first had k hits with a best significance >= s."""
    best = 0.0
    for i, h in enumerate(hits, 1):
        best = max(best, h["sig"])
        if i >= k and best >= s:
            return h["frame_time"]
    return None


# C3's field contains CCOR-1's, so a C3 claim overlaps ours in time; C2's field is
# inside CCOR-1's, so a comet leaves us for C2 and its C2 claim comes hours later.
LASCO_WINDOWS_H = {"c3": (1.0, 1.0), "c2": (1.0, 12.0)}  # (before our first hit, after our last hit)


def lasco_report(con, first: str, last: str, found: str, lat_s: float) -> dict | None:
    """The earliest LASCO C2/C3 claim whose frame times fit ours. Positions can't be
    compared across instruments, so this is only a hint, shown as such."""
    row = None
    for cam, (before, after) in LASCO_WINDOWS_H.items():
        lo = (datetime.fromisoformat(first) - timedelta(hours=before)).isoformat()
        hi = (datetime.fromisoformat(last) + timedelta(hours=after)).isoformat()
        cand = con.execute("""SELECT r.report_id, r.reporter, r.camera, r.status, r.report_timestamp
                              FROM reports r JOIN frames f USING (report_id)
                              WHERE r.camera = ? AND r.sighting_type = 'Potential Comet'
                                AND r.status != 'retracted' AND f.frame_time BETWEEN ? AND ?
                              ORDER BY r.report_timestamp LIMIT 1""", (cam, lo, hi)).fetchone()
        if cand is not None and (row is None or cand["report_timestamp"] < row["report_timestamp"]):
            row = cand
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
        if meta.get("n_hits") == d["n_hits"] and not thumb.exists():
            try:
                _thumbnail(camera, d, thumb)
            except Exception as e:
                print(f"thumbnail for {d['id']} failed: {e}", flush=True)
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
    for d in state.get("sparks", []):  # what the detector saw, for the two-hit chains worth a look
        thumb = tdir / f"{d['id']}_thumb.png"
        if not thumb.exists():
            try:
                _thumbnail(camera, d, thumb)
            except Exception as e:
                print(f"thumbnail for spark {d['id']} failed: {e}", flush=True)
        d["thumb"] = f"{camera}/tracks/{thumb.name}" if thumb.exists() else None
    _preview(camera, state, paths_, metas, out)
    keep = {p.name for d in state["tracks"] + state.get("sparks", [])
            for p in (tdir / f"{d['id']}.png", tdir / f"{d['id']}.json", tdir / f"{d['id']}_thumb.png")}
    for p in tdir.iterdir():
        if p.name not in keep:
            p.unlink()
    return drawn


THUMB_FRAMES = 8  # stacked along the track's velocity, like stage B


def _thumbnail(camera: str, d: dict, out: Path) -> None:
    """What the detector saw: the last THUMB_FRAMES significance maps shifted along
    the track's velocity and summed (divided by sqrt n), cut around the newest hit.
    A real object piles up into a dot; in a single raw frame a 7-sigma stack
    detection is invisible."""
    from .frameindex import FrameIndex

    last = d["hits"][-1]
    vx, vy = d["velocity"]
    t_last = _ts(last["t"])
    idx = FrameIndex(camera, datetime.fromisoformat(last["t"]).date() - timedelta(days=1))
    cands = [(p, t.timestamp()) for p, t in zip(idx.paths, idx.times)
             if t_last - 14 * 900 <= t.timestamp() <= t_last + 60]
    acc, n = np.zeros((2 * contact.HALF + 1,) * 2, np.float32), 0
    for p, t in cands[::-1]:
        m = A.load_meta(camera, p.name)
        if m is None or not m.ok or not A.sig_path(camera, p.name).exists():
            continue
        dt = (t_last - t) / 900.0
        sig = np.repeat(np.repeat(A.load_sig(camera, p.name), 2, axis=0), 2, axis=1)
        cut = contact._cut_report(sig, last["x"] - vx * dt, last["y"] - vy * dt, 1)
        acc += np.nan_to_num(cut, nan=0.0)
        n += 1
        if n >= THUMB_FRAMES:
            break
    if n == 0:
        return
    write_png(out, contact._zoom(contact._stretch(acc / np.sqrt(n), (-2, 8))))


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
    """Every camera's state.json, for /api/state, with the owner's labels attached."""
    cams = []
    for cam in cameras.CAMERAS:
        p = live_dir(cam) / "state.json"
        if p.exists():
            s = json.loads(p.read_text())
            lp = live_dir(cam) / "latest.json"
            s["latest"] = json.loads(lp.read_text()) if lp.exists() else None
            labels = load_labels(cam)
            for t in s["tracks"]:
                t["label"] = labels.get(t["id"])
            # a track only the comet label was holding leaves as soon as the label is cleared
            s["tracks"] = [t for t in s["tracks"] if not t.get("held") or (t["label"] or {}).get("label") == "comet"]
            cams.append(s)
    return {"now": datetime.now(timezone.utc).isoformat(timespec="seconds"), "cameras": cams}


# --- labels: the owner's eye, kept for training; the tracker never reads them -----

LABEL_VALUES = ("comet", "noise", "unsure", "clear")


def labels_dir(camera: str) -> Path:
    return live_dir(camera) / "labels"


def load_labels(camera: str) -> dict[str, dict]:
    """id -> latest label record ({label, at, contradicted?})."""
    p = labels_dir(camera) / "labels.json"
    return json.loads(p.read_text()) if p.exists() else {}


def record_label(camera: str, track_id: str, label: str) -> dict:
    """Append the label with a frozen copy of the track and its stacked cutout."""
    if camera not in cameras.CAMERAS or label not in LABEL_VALUES:
        raise ValueError("unknown camera or label")
    d = labels_dir(camera)
    d.mkdir(parents=True, exist_ok=True)
    state_p = live_dir(camera) / "state.json"
    tracks = json.loads(state_p.read_text())["tracks"] if state_p.exists() else []
    track = next((t for t in tracks if t["id"] == track_id), None)
    rec = {"id": track_id, "label": label, "at": _now_iso(), "track": track}
    with open(d / "labels.jsonl", "a") as f:
        f.write(json.dumps(rec) + "\n")
    thumb = live_dir(camera) / "tracks" / f"{track_id}_thumb.png"
    if thumb.exists():
        (d / f"{track_id}_stack.png").write_bytes(thumb.read_bytes())
    labels = load_labels(camera)
    if label == "clear":
        labels.pop(track_id, None)
    else:
        labels[track_id] = {"label": label, "at": rec["at"]}
    _write_json(d / "labels.json", labels)
    return {"id": track_id, "label": None if label == "clear" else labels[track_id]}


def contradict_label(camera: str, track_id: str, why: str) -> None:
    """A noise label the system now disagrees with: keep it, mark it, log it."""
    labels = load_labels(camera)
    rec = labels.get(track_id)
    if rec and rec["label"] == "noise" and not rec.get("contradicted"):
        rec["contradicted"] = {"why": why, "at": _now_iso()}
        _write_json(labels_dir(camera) / "labels.json", labels)
        with open(labels_dir(camera) / "labels.jsonl", "a") as f:
            f.write(json.dumps({"id": track_id, "label": "noise", "contradicted": rec["contradicted"]}) + "\n")
