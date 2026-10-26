import json
import threading
from datetime import datetime, timedelta, timezone
from urllib.request import urlopen

import numpy as np

from kreutz import live, score, serve
from kreutz.detect.stack import Peak
from kreutz.detect.track import Track


def _track(t0: datetime, n: int, x0=1500.0, y0=1200.0, vx=-8.0, vy=-3.0, sig=20.0):
    hits = [{"frame_time": (t0 + timedelta(minutes=15 * i)).isoformat(), "x": x0 + vx * i, "y": y0 + vy * i,
             "sig": sig, "vx": vx, "vy": vy, "edge": 100.0, "elong": 1.2} for i in range(n)]
    return {"id": 7, "camera": "ccor1", "status": "candidate", "merged": 0, "velocity": [vx, vy],
            "first_seen": hits[0]["frame_time"],
            "reached": {"3": hits[2]["frame_time"], "4": hits[3]["frame_time"]} if n >= 4 else {},
            "status_at": {"3": "candidate", "4": "candidate"} if n >= 4 else {},
            "star_drift": None, "sun_xy": [1041, 962], "hits": hits}


def test_track_key_is_stable_and_readable():
    t = _track(datetime(2026, 10, 23, 10, 45, 25, tzinfo=timezone.utc), 5, x0=1933, y0=1347)
    assert live.track_key(t) == "20261023T1045_1933_1347"


def test_summary_matches_report_and_computes_lead(tmp_path):
    t0 = datetime(2026, 10, 23, 10, 45, 25, tzinfo=timezone.utc)
    t = _track(t0, 6)
    times = np.array([(t0 + timedelta(minutes=15 * i)).timestamp() for i in range(6)])
    truth = score.Truth("report_cw_x", ["report_cw_x"], t0 + timedelta(hours=3), times,
                        np.array([h["x"] for h in t["hits"]]), np.array([h["y"] for h in t["hits"]]), "reported")
    from kreutz.reports import db

    con = db.connect(":memory:")
    con.execute("INSERT INTO reports (report_id, reporter, camera) VALUES (?, ?, ?)", ("report_cw_x", "Chris Wage", "ccor1"))
    d = live._track_summary(t, [truth], 22.6 * 60, con)
    assert d["live"] and d["n_hits"] == 6 and d["missing"] == 0
    assert d["report"]["reporter"] == "Chris Wage"
    # 3rd hit at t0+30min, +22.6 min latency, report posted at t0+3h -> found ~2.1 h before
    assert -2.3 < d["report"]["lead_h"] < -2.0
    unrep = live._track_summary(t, [], 0.0, con)
    assert unrep["report"] is None
    # a LASCO C3 claim overlapping in time is offered as a time-only match
    con.execute("INSERT INTO reports (report_id, reporter, camera, sighting_type, status, report_timestamp) "
                "VALUES (?,?,?,?,?,?)", ("report_cf_x", "Chuck Fields", "c3", "Potential Comet", "reported",
                                         (t0 + timedelta(hours=5)).isoformat()))
    con.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?)",
                ("report_cf_x", 1, (t0 + timedelta(hours=1)).isoformat(), 600, 700, None, None, "0145 600 700"))
    d = live._track_summary(t, [], 0.0, con)
    assert d["report"] is None  # still unreported on CCOR-1 ...
    assert d["lasco"]["camera"] == "C3" and d["lasco"]["reporter"] == "Chuck Fields"  # ... with a LASCO hint
    # a C3 claim hours away is not a hint
    con.execute("UPDATE frames SET frame_time=? WHERE report_id='report_cf_x'", ((t0 + timedelta(hours=9)).isoformat(),))
    assert live._track_summary(t, [], 0.0, con)["lasco"] is None


def test_build_state_splits_live_and_rejected(monkeypatch):
    now = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    fresh = _track(now - timedelta(hours=2), 5)
    old = _track(now - timedelta(hours=40), 5)
    bad = dict(_track(now - timedelta(hours=1), 4, x0=1300), status="rejected:extended",
               status_at={"3": "rejected:extended", "4": "rejected:extended"})
    monkeypatch.setattr(live.score, "latency_s", lambda cam: (0.0, "no log"))
    monkeypatch.setattr(live, "_reports", lambda con, cam, now: [])
    chain = Track(1, [Peak(**{k: v for k, v in dict(t=now.timestamp(), x=1.0, y=2.0, sig=6.0, vx=-8.0, vy=-3.0,
                                                   edge=None, elong=None).items()})] * 2)
    from kreutz.reports import db

    state = live.build_state("ccor1", [fresh, old, bad], [chain], [], [], None, db.connect(":memory:"), now)
    assert [t["n_hits"] for t in state["tracks"]] == [5]
    assert [t["status"] for t in state["rejected"]] == ["rejected:extended"]
    assert state["open_chains"] == 1 and state["longest"]["hits"] == 2
    assert state["window"] is None


def test_window_shrinks_after_a_gap():
    from kreutz import detect
    from kreutz.detect.frames import FrameMeta

    def meta(slot, ok=True):
        # frame times carry ~0.3 s of jitter, like the real files
        return FrameMeta(f"f{slot}", 1_700_000_000.0 + 900 * slot + 0.3 * (slot % 3), ok, [], None, None, 8, 0, None)

    # good frames in slots 0-5, a gap (6-15 bad), good again from 16
    metas = [meta(s, ok=not 6 <= s <= 15) for s in range(26)]
    # ending at 16: only 16 and 2..5 fit in 14 slots = 5 frames -> unusable
    assert detect.window_members(metas, 16) is None
    assert detect.window_members(metas, 18) is None
    # ending at 20: 20..16 (5) plus 5 would span 15 slots -> still unusable
    assert detect.window_members(metas, 20) is None
    # ending at 21: 8 frames 21..16,5,4 span 17 -> drop 4, then 5 -> 16..21: six frames, usable
    assert detect.window_members(metas, 21) == list(range(16, 22))
    # ending at 23: a full 8-frame window with no gap in it; jitter must not matter
    assert detect.window_members(metas, 23) == list(range(16, 24))


def test_group_objects_joins_satellites_and_continuations():
    t0 = datetime(2026, 10, 25, 1, 0, 25, tzinfo=timezone.utc)
    con = __import__("kreutz.reports.db", fromlist=["db"]).connect(":memory:")
    big = live._track_summary(_track(t0, 10, x0=1600, y0=1260, sig=57.0), [], 0.0, con)
    sat = live._track_summary(_track(t0 + timedelta(minutes=45), 3, x0=1600 - 24 + 30, y0=1260 - 9 + 12, sig=9.0), [], 0.0, con)
    cont = live._track_summary(_track(t0 + timedelta(minutes=15 * 12), 4, x0=1600 - 8 * 12 + 3, y0=1260 - 3 * 12 - 2, sig=8.0), [], 0.0, con)
    far = live._track_summary(_track(t0, 5, x0=1200, y0=900, sig=8.0), [], 0.0, con)
    tracks = [sat, big, cont, far]
    live.group_objects(tracks)
    assert big["group"] == big["id"] and sorted(big["pieces"]) == sorted([sat["id"], cont["id"]])
    assert sat["group"] == big["id"] and cont["group"] == big["id"] and sat["pieces"] == []
    assert far["group"] == far["id"] and far["pieces"] == []


def test_poll_phase_minimises_wait(monkeypatch):
    # files land 14..16 min after exposure start, a few stragglers at 22
    lags = [14.0, 14.5, 15.0, 15.5, 16.0] * 20 + [22.0] * 5
    monkeypatch.setattr(live.fetch, "publication_lags", lambda cam: lags)
    p = live.poll_phase("ccor1")
    assert p["phase_min"] == 1.0  # 16 min after the frame = minute 1 of the next slot
    assert p["expected_wait_min"] < 2
    monkeypatch.setattr(live.fetch, "publication_lags", lambda cam: None)
    assert live.poll_phase("ccor1")["phase_min"] is None


def test_next_poll_respects_guard_and_phase(monkeypatch):
    monkeypatch.setattr(live, "poll_phase", lambda cam: {"phase_min": 2.0})
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(live.net, "last_poll", lambda name: now.timestamp() - 14 * 60)
    wait = live.seconds_to_next_poll("ccor1")
    t = now + timedelta(seconds=round(wait))
    assert 60 <= wait <= 16 * 60
    assert t.minute % 15 == 2 and abs(t.second - 25) <= 2
    # last poll ran 5 s after its slot: the next slot is 5 s inside the guard, still take it
    slot = now.replace(minute=(now.minute // 15) * 15, second=25, microsecond=0) + timedelta(minutes=2)
    if slot <= now:
        slot += timedelta(minutes=15)
    monkeypatch.setattr(live.net, "last_poll", lambda name: (slot - timedelta(minutes=15)).timestamp() + 5)
    wait = live.seconds_to_next_poll("ccor1")
    t = now + timedelta(seconds=wait)
    assert abs((t - slot).total_seconds() - 6) <= 2  # the guard's 5 s plus 1


def test_tracker_snapshot_leaves_state_alone():
    from kreutz.detect.track import Tracker

    tr = Tracker()
    t0 = 1_700_000_000.0
    for k in range(5):
        tr.update(t0 + 900 * k, [Peak(t0 + 900 * k, 1500 - 8 * k, 1200 - 3 * k, -8.0, -3.0, 20.0, 100.0, 1.2)],
                  None, [1041, 962])
    a = tr.snapshot()
    b = tr.snapshot()
    assert len(a) == len(b) == 1 and a[0].merged == b[0].merged == 0
    assert len(tr.active) == 1  # still running
    tr.prune(t0 + 10 * 900)
    assert tr.done == []


def test_activity_log_rolls_and_tracks_current(tmp_path, monkeypatch):
    monkeypatch.setattr(live.paths, "DATA", tmp_path)
    a = live.Activity("ccor1")
    a.now("stage A", "working", 0, 3, ["CCOR1_1B_20261026T040025_V00_NC.fits"])
    since = a.current["since"]
    a.now("stage A", "working", 1, 3)
    assert a.current["since"] == since and a.current["done"] == 1  # same stage keeps its start time
    for i in range(live.Activity.MAX_EVENTS + 5):
        a.event(f"e{i}")
    saved = json.loads((tmp_path / "live" / "ccor1" / "activity.json").read_text())
    assert len(saved["events"]) == live.Activity.MAX_EVENTS and saved["events"][-1]["msg"].startswith("e")
    assert live.Activity("ccor1").events[-1]["msg"] == saved["events"][-1]["msg"]  # reloaded on restart
    assert live.merged_activity()["cameras"]["ccor1"]["current"]["stage"] == "stage A"


def test_server_routes(tmp_path, monkeypatch):
    monkeypatch.setattr(live.paths, "DATA", tmp_path)
    (tmp_path / "live" / "ccor1").mkdir(parents=True)
    (tmp_path / "live" / "ccor1" / "state.json").write_text(json.dumps({"camera": "ccor1", "tracks": []}))
    srv = serve.make_server("127.0.0.1", 0, tmp_path / "live")
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    port = srv.server_address[1]
    try:
        html = urlopen(f"http://127.0.0.1:{port}/").read().decode()
        assert "kreutz live" in html
        api = json.loads(urlopen(f"http://127.0.0.1:{port}/api/state").read())
        assert api["cameras"][0]["camera"] == "ccor1"
        raw = json.loads(urlopen(f"http://127.0.0.1:{port}/ccor1/state.json").read())
        assert raw["camera"] == "ccor1"
    finally:
        srv.shutdown()
