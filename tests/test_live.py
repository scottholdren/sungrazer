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
    state = live.build_state("ccor1", [fresh, old, bad], [chain], [], [], None, None, now)
    assert [t["n_hits"] for t in state["tracks"]] == [5]
    assert [t["status"] for t in state["rejected"]] == ["rejected:extended"]
    assert state["open_chains"] == 1 and state["longest"]["hits"] == 2
    assert state["window"] is None


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
