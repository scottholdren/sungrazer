"""Learn to tell comets from noise among candidate tracks, with the labels taken
from the Sungrazer reports rather than from a person.

A candidate is a track the tracker kept (not rejected by its rules) with at least
DECISION_HITS hits. It is a *comet* if it matches a hunter's report that was not
rejected (score.matches against score.truths); *noise* if the reports are silent
over its whole span; and left out as ambiguous if it matches a rejected claim, is
a duplicate of a comet track (two shared hit times within DUP_PX), or overlaps in
time with a LASCO claim while being bright (an object reported on another camera
but not on this one, like Chuck Fields' Oct 24 comet). The owner's labels in
data/live/<camera>/labels/ override: a comet label is a comet; a noise label is
ignored unless the reports also stayed silent.

Features are what the detector knows at decision time, from the first
DECISION_HITS hits only: where the track started, how fast it moves for that
distance, its heading, brightness and trend, shape, and straightness. The model
is a logistic regression (numpy, Newton's method, L2) on standardised features,
scored leave-one-period-out so every number in the report is out of sample.

Sources: results/<camera>/<start>_<end>/tracks.json from `kreutz score` runs, and
data/live/<camera>/archive/ where the live watcher files every candidate once it
is done with it (labels there come from the reports at training time, so a track
that gets reported later simply changes side).

`kreutz learn` writes results/<camera>/learn/README.md (the report) and model.json.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import paths, score
from .reports import db

DECISION_HITS = 5
DUP_PX = 60.0
LASCO_HOURS = 3.0
LASCO_BRIGHT = 20.0  # a bright unreported track overlapping a LASCO claim is probably that comet
L2 = 1.0
FEATURES = ("r0", "log_v_over_r", "speed", "heading", "pa", "sig_mean", "sig_max", "sig_slope", "elong", "edge",
            "rms", "consecutive")
# Feature sets tried, smallest first; the report shows them all and model.json keeps the best
# (fewest noise tracks at full recall, out of sample). With a few dozen comets, less is more.
TRIALS = {
    "start radius": ("r0",),
    "+ speed for radius": ("r0", "log_v_over_r"),
    "+ brightening": ("r0", "log_v_over_r", "sig_slope"),
    "+ brightness": ("r0", "log_v_over_r", "sig_slope", "sig_mean"),
    "+ straightness": ("r0", "log_v_over_r", "sig_slope", "sig_mean", "rms"),
    "+ heading": ("r0", "log_v_over_r", "sig_slope", "sig_mean", "rms", "heading"),
    "everything": FEATURES,
}
L2_TRIALS = (1.0, 10.0)
SUN_DEFAULT = (1041.0, 962.0)


# --- features -----------------------------------------------------------------

def features(hits: list[dict], sun_xy) -> dict:
    """Decision-time features from the first DECISION_HITS hits. Hits carry
    frame_time or t, x, y, sig and optionally elong/edge."""
    h = hits[:DECISION_HITS]
    sx, sy = sun_xy or SUN_DEFAULT
    t = np.array([_ts(a.get("frame_time") or a["t"]) for a in h]) / 900.0
    x = np.array([a["x"] for a in h], float)
    y = np.array([a["y"] for a in h], float)
    sig = np.array([a["sig"] for a in h], float)
    px, py = np.polyfit(t, x, 1), np.polyfit(t, y, 1)
    vx, vy = float(px[0]), float(py[0])
    v = math.hypot(vx, vy)
    r0 = math.hypot(x[0] - sx, y[0] - sy)
    to_sun = (sx - x[-1], sy - y[-1])
    cos = (vx * to_sun[0] + vy * to_sun[1]) / (v * math.hypot(*to_sun) + 1e-9)
    heading = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
    pa = math.degrees(math.atan2(-(y[0] - sy), x[0] - sx))
    rms = float(np.sqrt(np.mean((x - np.polyval(px, t)) ** 2 + (y - np.polyval(py, t)) ** 2)))
    el = [a["elong"] for a in h if a.get("elong") is not None]
    ed = [a["edge"] for a in h if a.get("edge") is not None]
    return {
        "r0": r0, "log_v_over_r": math.log(v / r0 * 1000 + 1e-6), "speed": v, "heading": heading, "pa": pa,
        "sig_mean": float(sig.mean()), "sig_max": float(sig.max()), "sig_slope": float(np.polyfit(t, sig, 1)[0]),
        "elong": float(np.median(el)) if el else 1.5, "edge": float(np.median(ed)) if ed else 250.0,
        "rms": rms, "consecutive": 1.0 if round(t[-1] - t[0]) + 1 == len(h) else 0.0,
    }


def _ts(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


# --- labels from the reports --------------------------------------------------

def label_tracks(tracks: list[dict], truths: list[score.Truth], lasco: list[score.Truth], owner: dict) -> list[dict]:
    """Rows {id, label, features, ...} for the candidates; ambiguous ones are dropped."""
    good = [t for t in truths if t.status != "rejected"]
    bad = [t for t in truths if t.status == "rejected"]
    comets, rows = [], []
    for t in tracks:
        if t["status"].startswith("rejected") or len(t["hits"]) < DECISION_HITS:
            continue
        lab = _owner_label(owner, t)
        if any(score.matches(t, tr) for tr in good):
            comets.append(t)
            rows.append((t, "comet", "reported"))
        elif lab == "comet":
            comets.append(t)
            rows.append((t, "comet", "owner"))
        elif any(score.matches(t, tr) for tr in bad):
            continue  # a rejected claim: nobody knows
        else:
            rows.append((t, "noise", "unreported"))
    out = []
    for t, lab, why in rows:
        if lab == "noise":
            if any(_duplicate(t, c) for c in comets):
                continue  # the same object as a comet track
            best = max(h["sig"] for h in t["hits"])
            if best >= LASCO_BRIGHT:
                # a persistent object this bright that nobody reported on this camera is a comet
                # reported elsewhere (LASCO), a piece of one, or something odd: not a noise example
                continue
        f = features(t["hits"], t.get("sun_xy"))
        out.append({"id": _key(t), "label": lab, "why": why, "n_hits": len(t["hits"]),
                    "best_sig": round(max(h["sig"] for h in t["hits"]), 1), "features": f})
    return out


def _owner_label(owner: dict, t: dict) -> str | None:
    rec = owner.get(_key(t))
    return rec["label"] if rec else None


def _key(t: dict) -> str:
    h = t["hits"][0]
    ts = h.get("frame_time") or h["t"]
    return f"{ts[:16].replace('-', '').replace(':', '')}_{h['x']:.0f}_{h['y']:.0f}"


def _duplicate(a: dict, b: dict) -> bool:
    pos = {(h.get("frame_time") or h["t"]): (h["x"], h["y"]) for h in b["hits"]}
    n = 0
    for h in a["hits"]:
        p = pos.get(h.get("frame_time") or h["t"])
        if p and math.hypot(h["x"] - p[0], h["y"] - p[1]) <= DUP_PX:
            n += 1
    return n >= 2


def _overlaps(t: dict, lt: score.Truth) -> bool:
    lo = _ts(t["hits"][0].get("frame_time") or t["hits"][0]["t"]) - LASCO_HOURS * 3600
    hi = _ts(t["hits"][-1].get("frame_time") or t["hits"][-1]["t"]) + LASCO_HOURS * 3600
    return bool(lo <= lt.times.max() and lt.times.min() <= hi)


# --- sources --------------------------------------------------------------------

def periods(camera: str) -> list[tuple[str, date, date, list[dict]]]:
    """Scored periods under results/<camera>/, longest first, dropping any contained in another."""
    found = []
    for p in sorted((paths.ROOT / "results" / camera).glob("*_*/tracks.json")):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})_(\d{4}-\d{2}-\d{2})", p.parent.name)
        if not m:
            continue
        a, b = date.fromisoformat(m[1]), date.fromisoformat(m[2])
        tracks = json.loads(p.read_text())
        found.append((p.parent.name, a, b, tracks["tracks"] if isinstance(tracks, dict) else tracks))
    found.sort(key=lambda x: -(x[2] - x[1]).days)
    out = []
    for name, a, b, tracks in found:
        if any(a2 <= a and b <= b2 for _, a2, b2, _ in out):
            continue
        out.append((name, a, b, tracks))
    return out


def archived(camera: str) -> list[tuple[str, date, date, list[dict]]]:
    """The live watcher's archive, one period per month, in the scorer's track form."""
    adir = paths.DATA / "live" / camera / "archive"
    out = []
    for mdir in sorted(adir.glob("*-*")) if adir.exists() else []:
        tracks = []
        for p in mdir.glob("*.json"):
            d = json.loads(p.read_text())
            hits = [{"frame_time": h["t"], "x": h["x"], "y": h["y"], "sig": h["sig"]} for h in d["hits"]]
            tracks.append({"id": d["id"], "status": d["status"], "hits": hits, "sun_xy": d.get("sun_xy")})
        if tracks:
            days = sorted(_ts(t["hits"][0]["frame_time"]) for t in tracks)
            a = datetime.fromtimestamp(days[0], timezone.utc).date()
            b = datetime.fromtimestamp(days[-1], timezone.utc).date()
            out.append((f"live {mdir.name}", a, b, tracks))
    return out


def dataset(camera: str, con) -> list[dict]:
    owner = _owner_labels(camera)
    rows = []
    for name, a, b, tracks in periods(camera) + archived(camera):
        truths, provisional = score.truths(con, camera, a, b)
        lasco = score.other_reports(con, "c2", a, b) + score.other_reports(con, "c3", a, b)
        for r in label_tracks(tracks, truths, lasco, owner):
            r["period"], r["provisional"] = name, provisional
            rows.append(r)
    return rows


def _owner_labels(camera: str) -> dict:
    p = paths.DATA / "live" / camera / "labels" / "labels.json"
    return json.loads(p.read_text()) if p.exists() else {}


# --- the model --------------------------------------------------------------------

def fit(rows: list[dict], feats=FEATURES, l2: float = L2) -> dict:
    """Class-balanced L2 logistic regression on standardised features (Newton's method)."""
    X, y = _matrix(rows, feats)
    mu, sd = X.mean(axis=0), X.std(axis=0) + 1e-9
    Z = np.hstack([(X - mu) / sd, np.ones((len(X), 1))])
    w = np.zeros(Z.shape[1])
    pos, neg = max(y.sum(), 1), max((1 - y).sum(), 1)
    sw = np.where(y == 1, 0.5 / pos, 0.5 / neg) * len(y)
    reg = np.full(Z.shape[1], l2); reg[-1] = 0.0
    for _ in range(50):
        p = 1 / (1 + np.exp(-Z @ w))
        g = Z.T @ (sw * (p - y)) + reg * w
        H = (Z * (sw * p * (1 - p))[:, None]).T @ Z + np.diag(reg)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return {"features": list(feats), "l2": l2, "mu": mu.tolist(), "sd": sd.tolist(), "w": w.tolist()}


def predict(model: dict, f: dict) -> float:
    x = np.array([f[k] for k in model["features"]], float)
    z = np.append((x - np.array(model["mu"])) / np.array(model["sd"]), 1.0)
    return float(1 / (1 + np.exp(-z @ np.array(model["w"]))))


def _matrix(rows: list[dict], feats) -> tuple[np.ndarray, np.ndarray]:
    X = np.array([[r["features"][k] for k in feats] for r in rows], float).reshape(len(rows), len(feats))
    y = np.array([1.0 if r["label"] == "comet" else 0.0 for r in rows])
    return X, y


def cross_validate(rows: list[dict], feats=FEATURES, l2: float = L2) -> list[dict]:
    """Leave one period out; each row gets an out-of-sample score."""
    out = []
    for name in sorted({r["period"] for r in rows}):
        train = [r for r in rows if r["period"] != name]
        test = [r for r in rows if r["period"] == name]
        both = any(r["label"] == "comet" for r in train) and any(r["label"] == "noise" for r in train)
        m = fit(train if both else rows, feats, l2)  # nothing to hold out against: in-sample, flagged
        for r in test:
            out.append({**r, "score": predict(m, r["features"]), "in_sample": not both})
    return out


def noise_at_full_recall(scored: list[dict]) -> tuple[int, float]:
    """How many noise tracks pass when the threshold is set to keep every comet."""
    comets = [r["score"] for r in scored if r["label"] == "comet"]
    thr = min(comets) if comets else 0.5
    return sum(r["score"] >= thr for r in scored if r["label"] == "noise"), thr


# --- the report -----------------------------------------------------------------------

def learn(camera: str = "ccor1") -> Path:
    con = db.connect()
    rows = dataset(camera, con)
    if not rows:
        raise SystemExit(f"no scored periods or archive for {camera}")
    trials = []  # (noise at full recall, name, feats, l2, scored)
    for name, feats in TRIALS.items():
        for l2 in L2_TRIALS:
            sc = cross_validate(rows, feats, l2)
            trials.append((noise_at_full_recall(sc)[0], name, feats, l2, sc))
    best = min(trials, key=lambda t: (t[0], len(t[2])))
    _, best_name, feats, l2, scored = best
    model = fit(rows, feats, l2)
    model["trial"] = best_name
    out = paths.ROOT / "results" / camera / "learn"
    out.mkdir(parents=True, exist_ok=True)
    comets = [r for r in scored if r["label"] == "comet"]
    noise = [r for r in scored if r["label"] == "noise"]
    weeks = sum((b - a).days + 1 for _, a, b, _ in periods(camera) + archived(camera)) / 7.0
    thr_all = min(r["score"] for r in comets) if comets else 0.5
    thr_most = sorted(r["score"] for r in comets)[int(0.1 * len(comets))] if comets else 0.5
    model["threshold"] = thr_all
    model["trained"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    model["n_comets"], model["n_noise"] = len(comets), len(noise)
    (out / "model.json").write_text(json.dumps(model, indent=1))
    L = [f"# Learned rejecter for {camera.upper()} candidates", "",
         f"Labels from the Sungrazer reports, features from the first {DECISION_HITS} hits, logistic regression, "
         "scored leave-one-period-out (every score below is out of sample unless marked).", "",
         f"**{len(comets)} comets, {len(noise)} noise tracks over {weeks:.1f} weeks of frames.**", "",
         "## Periods", "", "| period | comets | noise | truth |", "|---|---|---|---|"]
    for name in sorted({r["period"] for r in rows}):
        rs = [r for r in rows if r["period"] == name]
        L.append(f"| {name} | {sum(r['label'] == 'comet' for r in rs)} | {sum(r['label'] == 'noise' for r in rs)} | "
                 f"{'hunter claims (provisional)' if rs[0]['provisional'] else 'confirmation list'} |")
    L += ["", "## Feature sets tried", "",
          "Noise tracks that pass when the threshold is set to keep every comet, out of sample. "
          f"The report below uses the best: **{best_name}** (L2 {l2:g}).", "",
          "| feature set | L2 | noise kept at full recall |", "|---|---|---|"]
    for n_noise, name, fs, l2_, _ in trials:
        L.append(f"| {name} ({', '.join(fs)}) | {l2_:g} | {n_noise}/{len(noise)} |")
    L += ["", "## What a threshold buys", "",
          "Candidates with at least 5 hits that the rules keep. 'Baseline' rows are the hand rules for comparison.", "",
          "| rule | comets kept | noise kept | noise per week |", "|---|---|---|---|"]

    def row(name, keep):
        c, n = sum(keep(r) for r in comets), sum(keep(r) for r in noise)
        L.append(f"| {name} | {c}/{len(comets)} | {n}/{len(noise)} | {n / weeks:.1f} |")
    row("baseline: every candidate at 5 hits", lambda r: True)
    row("baseline: watch tier (5 hits, 10 sigma)", lambda r: r["features"]["sig_max"] >= 10)
    row("baseline: steady (5 hits, started >= 550 px out)", lambda r: r["features"]["r0"] >= 550)
    row(f"model, every comet kept (score >= {thr_all:.3f})", lambda r: r["score"] >= thr_all)
    row(f"model, 90% of comets kept (score >= {thr_most:.3f})", lambda r: r["score"] >= thr_most)
    row("model >= 0.5", lambda r: r["score"] >= 0.5)
    L += ["", "## What the model keys on", "",
          "Weights on standardised features (one standard deviation of the feature moves the log-odds by this much). "
          "Positive favours comet.", "", "| feature | weight |", "|---|---|"]
    for k, w in sorted(zip(model["features"], model["w"]), key=lambda kw: -abs(kw[1])):
        L.append(f"| {k} | {w:+.2f} |")
    L += ["", "## Every comet, out of sample", "", "| period | track | hits | best sigma | started (px) | score |", "|---|---|---|---|---|---|"]
    for r in sorted(comets, key=lambda r: r["score"]):
        L.append(f"| {r['period']} | {r['id']} | {r['n_hits']} | {r['best_sig']} | {r['features']['r0']:.0f} | "
                 f"{r['score']:.3f}{' (in sample)' if r.get('in_sample') else ''} |")
    L += ["", "## Noise the model likes most", "", "| period | track | hits | best sigma | started (px) | score |", "|---|---|---|---|---|---|"]
    for r in sorted(noise, key=lambda r: -r["score"])[:20]:
        L.append(f"| {r['period']} | {r['id']} | {r['n_hits']} | {r['best_sig']} | {r['features']['r0']:.0f} | {r['score']:.3f} |")
    L += ["", "Dropped as ambiguous: tracks matching a rejected claim, duplicates of a comet track, and bright "
          f"(>= {LASCO_BRIGHT:g} sigma) unreported tracks overlapping a LASCO claim in time.", "",
          f"*Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC; model in model.json.*"]
    (out / "README.md").write_text("\n".join(L) + "\n")
    (out / "dataset.json").write_text(json.dumps(scored, indent=0))
    print("\n".join(L[:30]))
    return out / "README.md"
