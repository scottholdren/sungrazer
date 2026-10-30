from datetime import datetime, timedelta, timezone

import numpy as np

from kreutz import learn, score


def _hits(t0, n, x0, y0, vx, vy, sig, step=1):
    return [{"frame_time": (t0 + timedelta(minutes=15 * step * i)).isoformat(), "x": x0 + vx * i, "y": y0 + vy * i,
             "sig": sig + 0.1 * i, "elong": 1.5, "edge": 300.0} for i in range(n)]


def _track(hits, status="candidate"):
    return {"id": 1, "status": status, "hits": hits, "sun_xy": [1041, 962]}


def test_features_are_decision_time_only():
    t0 = datetime(2026, 10, 29, 21, 15, 25, tzinfo=timezone.utc)
    f = learn.features(_hits(t0, 9, 1534.5, 1282.5, -8.0, -4.0, 7.0), [1041, 962])
    assert abs(f["r0"] - 589.4) < 1 and abs(f["speed"] - 8.94) < 0.05 and f["consecutive"] == 1.0
    assert f["rms"] < 1e-6 and f["heading"] < 10 and -45 < f["pa"] < -30
    assert abs(f["sig_slope"] - 0.1) < 1e-6 and abs(f["sig_mean"] - 7.2) < 1e-6  # first five hits only
    g = learn.features(_hits(t0, 5, 1534.5, 1282.5, -8.0, -4.0, 7.0, step=2), [1041, 962])
    assert g["consecutive"] == 0.0


def test_labels_come_from_reports_and_ambiguous_ones_are_dropped():
    t0 = datetime(2026, 10, 23, 9, 0, 25, tzinfo=timezone.utc)
    comet = _track(_hits(t0, 8, 1500, 1200, -8, -3, 9.0))
    dup = _track(_hits(t0, 6, 1520, 1230, -8, -3, 7.0))             # 36 px off the comet: the same object
    noise = _track(_hits(t0, 6, 1300, 1100, -8, -3, 7.0))
    bright = _track(_hits(t0, 6, 1700, 1500, -8, -3, 30.0))        # unreported but 30 sigma: not a noise example
    short = _track(_hits(t0, 4, 1400, 1150, -8, -3, 7.0))
    rejected = _track(_hits(t0, 6, 900, 1300, -8, -3, 7.0), status="rejected:outbound")
    claimed = _track(_hits(t0, 6, 1600, 1000, -8, -3, 7.0))
    times = np.array([(t0 + timedelta(minutes=15 * i)).timestamp() for i in range(8)])
    truth = score.Truth("report_cw", ["report_cw"], t0, times, np.array([h["x"] for h in comet["hits"]]),
                        np.array([h["y"] for h in comet["hits"]]), "reported")
    bad = score.Truth("report_x", ["report_x"], t0, times[:6], np.array([h["x"] for h in claimed["hits"]]),
                      np.array([h["y"] for h in claimed["hits"]]), "rejected")
    rows = learn.label_tracks([comet, dup, noise, bright, short, rejected, claimed], [truth, bad], [], {})
    assert [(r["label"], r["why"]) for r in rows] == [("comet", "reported"), ("noise", "unreported")]
    assert rows[1]["id"] == "20261023T0900_1300_1100"
    # the owner's comet label makes an unreported track a comet
    rows = learn.label_tracks([noise], [], [], {"20261023T0900_1300_1100": {"label": "comet"}})
    assert [(r["label"], r["why"]) for r in rows] == [("comet", "owner")]


def test_fit_separates_and_cross_validation_is_out_of_sample():
    rng = np.random.default_rng(1)
    rows = []
    for period in ("a", "b", "c"):
        for k in range(30):
            comet = k < 6
            f = {name: float(rng.normal()) for name in learn.FEATURES}
            f["r0"] = float(rng.normal(900 if comet else 450, 80))
            rows.append({"period": period, "label": "comet" if comet else "noise", "features": f})
    m = learn.fit(rows, ("r0", "speed"))
    assert m["w"][0] > 1.0 and abs(m["w"][1]) < 0.5  # r0 carries it, the random feature does not
    assert learn.predict(m, {"r0": 950.0, "speed": 0.0}) > 0.9 > 0.2 > learn.predict(m, {"r0": 400.0, "speed": 0.0})
    scored = learn.cross_validate(rows, ("r0", "speed"))
    assert len(scored) == len(rows) and not any(r["in_sample"] for r in scored)
    n_noise, thr = learn.noise_at_full_recall(scored)
    assert n_noise <= 3
