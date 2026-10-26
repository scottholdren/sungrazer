"""`kreutz score`: grade detector tracks against the Sungrazer answer key.

Truth objects
  * a confirmed comet = its confirmed report plus that report's follow-ups
    (additional observations / corrections). Its "first human post" is the
    earliest posting time in that group.
  * where no confirmation list covers the window yet (recent data), we fall
    back to every non-retracted "Potential Comet" claim with >= 3 positions,
    clearly labelled PROVISIONAL.

A track matches a truth object when at least MATCH_HITS of its hits lie within
MATCH_PX of the object's straight-line path (fitted to the hunters' positions)
at the same time, within +-MATCH_HOURS of the hunters' first/last frame.

A track counts at threshold k only if it was submittable then: its status
when it got its k-th hit (status_at), not its final status. A rule that fires
later (glare-zone as the comet nears the occulter, say) is listed but doesn't
un-find the comet.

Lead time at threshold k = (exposure start of the newest frame in the window
where the track got its k-th hit) - (first human posting time); negative = we
would have been first. "Real" lead adds what that frame time leaves out:
SWPC's publication lag (median, measured from the live listing), half our
15-minute poll interval on average, and PROCESSING_S to run the new window.

Reports whose positions can't be mapped to CCOR pixels (coords.report_mapping)
have no positions and so never become truth objects; they're counted.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import detect
from .detect import frames as A
from .reports import db

_MON = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
                                    "Sep", "Oct", "Nov", "Dec"], 1)}
MATCH_PX = 12.0
MATCH_HITS = 2
MATCH_HOURS = 3.0
THRESHOLDS = (3, 4, 5)
PROCESSING_S = 30.0  # stage A on one new frame (27 s, incl. reading its history) + stage B on its window (2 s); 2026-10-25


def live_at(track: dict, k: int) -> bool:
    return track.get("status_at", {}).get(str(k)) == "candidate"


def latency_s(camera: str) -> tuple[float, str]:
    """Seconds between a frame's exposure start and our verdict on its window."""
    from . import fetch, net

    lag = fetch.publication_lag(camera)
    pub = lag["median_min"] * 60 if lag else 0.0
    total = pub + net.POLL_INTERVAL_S / 2 + PROCESSING_S
    src = lag["text"] if lag else "no publication log yet (run `kreutz fetch --live`); publication lag taken as 0"
    return total, (f"latency {total / 60:.1f} min = publication {pub / 60:.1f} + poll wait {net.POLL_INTERVAL_S / 120:.1f}"
                   f" + processing {PROCESSING_S / 60:.1f}. {src}")


@dataclass
class Truth:
    label: str  # e.g. "CCR1 0074" or a report id
    report_ids: list[str]
    first_post: datetime
    times: np.ndarray  # unix seconds
    xs: np.ndarray
    ys: np.ndarray
    status: str
    fit: tuple = field(init=False)

    def __post_init__(self):
        t = self.times / 900.0
        if np.ptp(t) > 0:
            self.fit = (np.polyfit(t, self.xs, 1), np.polyfit(t, self.ys, 1))
        else:
            self.fit = (np.array([0.0, self.xs.mean()]), np.array([0.0, self.ys.mean()]))

    def position(self, ts: float) -> tuple[float, float] | None:
        if not (self.times.min() - MATCH_HOURS * 3600 <= ts <= self.times.max() + MATCH_HOURS * 3600):
            return None
        return float(np.polyval(self.fit[0], ts / 900.0)), float(np.polyval(self.fit[1], ts / 900.0))


def _positions(con, report_ids):
    q = ",".join("?" * len(report_ids))
    rows = con.execute(f"SELECT frame_time, x_ul, y_ul FROM frames WHERE report_id IN ({q}) "
                       f"AND x_ul IS NOT NULL AND frame_time IS NOT NULL", report_ids).fetchall()
    return (np.array([datetime.fromisoformat(r[0]).timestamp() for r in rows]),
            np.array([r[1] for r in rows]), np.array([r[2] for r in rows]))


def _clean(times, xs, ys):
    """Drop positions far off the group's line (hunter typos: wrong date/time/digit)."""
    if len(times) < 4:
        return times, xs, ys
    t = times / 900.0
    keep = np.ones(len(t), bool)
    for _ in range(2):
        px, py = np.polyfit(t[keep], xs[keep], 1), np.polyfit(t[keep], ys[keep], 1)
        r = np.hypot(xs - np.polyval(px, t), ys - np.polyval(py, t))
        keep = r < max(10.0, 3 * np.median(r[keep]))
    return times[keep], xs[keep], ys[keep]


def _anchored(con, anchor: str, group: list[str]):
    """Positions of the anchor (confirmed) report, plus follow-up positions that
    lie on its line. Follow-ups with a typo'd date or time (e.g. CCR1 0076's,
    logged a day early) would otherwise wreck the fitted path."""
    t, x, y = _clean(*_positions(con, [anchor]))
    if len(t) < 2:
        return _clean(*_positions(con, group))
    tt = t / 900.0
    fx, fy = np.polyfit(tt, x, 1), np.polyfit(tt, y, 1)
    for rid in group[1:]:
        t2, x2, y2 = _positions(con, [rid])
        ok = np.hypot(x2 - np.polyval(fx, t2 / 900.0), y2 - np.polyval(fy, t2 / 900.0)) < 15
        t, x, y = np.concatenate([t, t2[ok]]), np.concatenate([x, x2[ok]]), np.concatenate([y, y2[ok]])
    return t, x, y


_CLAIM = {"reported": "pending", "known": "pending"}


def truths(con, camera: str, start: date, end: date) -> tuple[list[Truth], bool]:
    """Truth objects whose hunter positions start in [start, end]. Returns (truths, provisional)."""
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    out = []
    for c in con.execute("""SELECT c.series, c.number, c.report_id FROM confirmations c
                            JOIN reports r USING (report_id)
                            WHERE r.camera = ? AND r.image_date >= ? AND r.image_date < ?
                            ORDER BY c.number""", (camera, lo, hi)):
        group = [c["report_id"]] + [r[0] for r in con.execute(
            "SELECT report_id FROM reports WHERE parent_id = ? AND sighting_type != 'Retracting Report'",
            (c["report_id"],))]
        t, x, y = _anchored(con, c["report_id"], group)
        if len(t) < 2:
            continue
        posts = [datetime.fromisoformat(r[0]) for r in con.execute(
            f"SELECT report_timestamp FROM reports WHERE report_id IN ({','.join('?' * len(group))})", group)]
        out.append(Truth(f"{c['series']} {c['number']}", group, min(posts), t, x, y, "confirmed"))
    if out:
        return out, False
    # No confirmation list covers this window: provisional truth from hunter claims.
    # Rejected claims are kept (labelled) so the split is visible; recall ignores them.
    for r in con.execute("""SELECT report_id, report_timestamp, status FROM reports
                            WHERE camera = ? AND image_date >= ? AND image_date < ?
                              AND sighting_type = 'Potential Comet' AND status != 'retracted'
                            ORDER BY report_timestamp""", (camera, lo, hi)):
        group = [r[0]] + [k[0] for k in con.execute(
            "SELECT report_id FROM reports WHERE parent_id = ? AND sighting_type != 'Retracting Report'", (r[0],))]
        t, x, y = _clean(*_positions(con, group))
        if len(t) < 3:
            continue
        out.append(Truth(r[0], group, datetime.fromisoformat(r[1]), t, x, y, _CLAIM.get(r[2], r[2])))
    return out, True


def images_of_range(s: str, year: int | None = None) -> tuple[date, date] | None:
    """'Jul23-25,25' / 'Jul31-Aug01,25' / 'Jul08,25' -> (first, last) dates.

    The year suffix is sometimes mistyped in the lists ('Jul24-25.26', 'Jul02-03,35'),
    so callers pass the year of the confirmation's posting time when they have it."""
    m = re.match(r"([A-Z][a-z]{2})(\d{1,2})(?:-([A-Z][a-z]{2})?(\d{1,2}))?[,.](\d{2})", s or "")
    if not m or m.group(1) not in _MON:
        return None
    mon1, d1, mon2, d2, yy = m.groups()
    year = year or 2000 + int(yy)
    a = date(year, _MON[mon1], int(d1))
    b = date(year, _MON.get(mon2 or mon1, _MON[mon1]), int(d2 or d1))
    return a, b


def g1_visible_soho(con, start: date, end: date) -> list[tuple[str, date, date]]:
    """Comets on the SOHO list (discovered in LASCO) that the admins say were also
    seen in G1 = CCOR-1. Their reports are in LASCO pixels, so a CCOR-1 track can
    only be compared with them by time, not position."""
    out = []
    for c in con.execute("SELECT number, telescopes, images_of, post_time FROM confirmations WHERE series='SOHO'"):
        if "G1" not in (c["telescopes"] or "").split(","):
            continue
        r = images_of_range(c["images_of"], datetime.fromisoformat(c["post_time"]).year)
        if r and r[0] <= end and r[1] >= start:
            out.append((f"SOHO {c['number']}", *r))
    return out


def other_reports(con, camera: str, start: date, end: date) -> list[Truth]:
    """Every report with >= 2 positions (any status), to annotate unmatched tracks."""
    lo, hi = (start - timedelta(days=1)).isoformat(), (end + timedelta(days=2)).isoformat()
    out = []
    for r in con.execute("SELECT report_id, report_timestamp, status FROM reports WHERE camera=? "
                         "AND image_date >= ? AND image_date < ?", (camera, lo, hi)):
        t, x, y = _positions(con, [r[0]])
        if len(t) >= 2:
            out.append(Truth(r[0], [r[0]], datetime.fromisoformat(r[1]), t, x, y, r[2]))
    return out


def matches(track: dict, truth: Truth) -> bool:
    n = 0
    for h in track["hits"]:
        p = truth.position(datetime.fromisoformat(h["frame_time"]).timestamp())
        if p is not None and np.hypot(h["x"] - p[0], h["y"] - p[1]) <= MATCH_PX:
            n += 1
    return n >= MATCH_HITS


def _hours(td: timedelta) -> str:
    h = td.total_seconds() / 3600
    return f"{h:+.1f} h"


def results_dir(camera: str, start: date, end: date):
    from .paths import RESULTS

    return RESULTS / camera / f"{start}_{end}"


def score(camera: str, start: date, end: date, workers=None, sheets: bool = True,
          seed_cold: bool = detect.SEED_COLD) -> dict:
    """Grade, print the scorecard, and save it under results/<camera>/<start>_<end>/:
    scorecard.txt (everything printed), tracks.json (tracks surviving the
    rejection rules) and sheets/ (every true positive and every unmatched track
    that reached 5 hits). All sheets also go to data/derived/.../sheets_*/."""
    out = results_dir(camera, start, end)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "scorecard.txt", "w") as f, _tee(f):
        print(f"# kreutz score --camera {camera} --start {start} --end {end}" + (" --seed-cold" if seed_cold else ""))
        print(f"# code {_git_rev()}, stage A {A.VERSION}, stage B {detect.B.VERSION}, "
              f"run {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
        return _score(camera, start, end, workers, sheets, out, seed_cold)


def _git_rev() -> str:
    import subprocess

    from .paths import ROOT

    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, check=True).stdout.strip()
        # results/ is rewritten by the run itself, so it doesn't count as uncommitted code
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", ".", ":!results"],
                               capture_output=True, text=True, check=True).stdout.strip()
        return rev + ("-dirty" if dirty else "")
    except Exception:
        return "unknown"


class _tee:
    """Copy everything printed to stdout into a file as well."""

    def __init__(self, f):
        self.f = f

    def __enter__(self):
        import sys

        self.stdout = sys.stdout
        sys.stdout = self
        return self

    def __exit__(self, *exc):
        import sys

        sys.stdout = self.stdout

    def write(self, s):
        self.stdout.write(s)
        self.f.write(s)

    def flush(self):
        self.stdout.flush()
        self.f.flush()


def _score(camera, start, end, workers, sheets, results, seed_cold=detect.SEED_COLD) -> dict:
    tracks = detect.run(camera, start, end, workers=workers, seed_cold=seed_cold)
    tracks = [detect.track_dict(t, camera) for t in tracks]
    con = db.connect()
    truth, provisional = truths(con, camera, start, end)
    others = other_reports(con, camera, start, end)
    live = [t for t in tracks if any(live_at(t, k) for k in THRESHOLDS)]  # submittable at some threshold
    lat, lat_text = latency_s(camera)
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    unmapped = con.execute("SELECT report_id, reporter, image_size, status FROM reports WHERE camera=? AND "
                           "image_date >= ? AND image_date < ? AND mapping='unmapped' AND "
                           "sighting_type='Potential Comet' AND status != 'retracted'", (camera, lo, hi)).fetchall()
    transformed = con.execute("SELECT report_id FROM reports WHERE camera=? AND image_date >= ? AND image_date < ? "
                              "AND mapping='transform'", (camera, lo, hi)).fetchall()

    print()
    print(f"=== {camera} {start}..{end}: {len(truth)} truth objects"
          + (" (PROVISIONAL: no confirmation list covers this window; using hunter claims)" if provisional else ""))
    if unmapped:
        print(f"    left out, positions not mappable to CCOR pixels: "
              + ", ".join(f"{r[0]} ({r[1]}, {r[2] or 'no size'}, {r[3]})" for r in unmapped))
    if transformed:
        print(f"    mapped with a fitted reporter transform (coords.REPORT_TRANSFORMS): "
              + ", ".join(r[0] for r in transformed))
    print(f"    {len(tracks)} candidate tracks, {len(live)} submittable at 3, 4 or 5 hits; final status of all: "
          f"{len(tracks) - sum(1 for t in tracks if t['status'].startswith('rejected'))} live, "
          f"{', '.join(f'{k}: {v}' for k, v in _count_rejections(tracks).items())}")
    print(f"    {lat_text}")
    print()
    counted = [tr for tr in truth if tr.status != "rejected"]
    hdr = f"{'object':<26} {'status':<9} {'first human post':<17} {'found':<6} " + " ".join(
        f"{f'@{k} hits: time / lead / real':<36}" for k in THRESHOLDS) + " best sig"
    print(hdr)
    print("-" * len(hdr))
    found_tracks = set()
    rows = []
    for tr in truth:
        allm = [t for t in tracks if matches(t, tr)]
        m = [t for t in allm if t in live]
        found_tracks.update(t["id"] for t in m)
        cells = []
        for k in THRESHOLDS:
            reached = [datetime.fromisoformat(t["reached"][str(k)]) for t in m if live_at(t, k)]
            if reached:
                when = min(reached)
                cells.append(f"{when:%m-%d %H:%M} / {_hours(when - tr.first_post)} / "
                             f"{_hours(when + timedelta(seconds=lat) - tr.first_post)}")
            else:
                cells.append("-")
        best = max((h["sig"] for t in m for h in t["hits"]), default=float("nan"))
        rows.append((tr, m, allm))
        print(f"{tr.label:<26} {tr.status:<9} {tr.first_post:%m-%d %H:%M}        {'yes' if m else 'NO':<6} "
              + " ".join(f"{c:<36}" for c in cells) + f" {best:5.1f}")
    print("(lead = our frame time - first human post; real = lead + latency above; negative = we would have been first)")

    print()
    print("status of every track matching a truth object, when it reached 3 / 4 / 5 hits, and at the end:")
    for tr, m, allm in rows:
        if not allm:
            print(f"  {tr.label:<26} no track at all")
        for t in sorted(allm, key=lambda t: t["hits"][0]["frame_time"]):
            at = " / ".join(t.get("status_at", {}).get(str(k), "-") for k in THRESHOLDS)
            print(f"  {tr.label:<26} track {t['id']:>6} ({len(t['hits'])} hits, {t['merged']} merged): {at}; "
                  f"final {t['status']}")

    print()
    print(f"{'threshold':<10} {'tracks':>6} {'match truth':>11} {'precision':>9} {'recall':>8}")
    summary = {}
    for k in THRESHOLDS:
        at_k = [t for t in tracks if live_at(t, k)]
        tp = [t for t in at_k if t["id"] in found_tracks]
        rec = sum(1 for tr, m, _ in rows if tr.status != "rejected" and any(live_at(t, k) for t in m))
        prec = len(tp) / len(at_k) if at_k else float("nan")
        summary[k] = {"tracks": len(at_k), "tp": len(tp), "precision": prec, "recall": rec / max(len(counted), 1)}
        print(f"{k:>3} hits   {len(at_k):>6} {len(tp):>11} {prec:>9.2f} {rec:>4}/{len(counted):<3}")
    if provisional:
        by = {}
        for tr, m, _ in rows:
            by.setdefault(tr.status, [0, 0])[0] += bool(m)
            by[tr.status][1] += 1
        print("claims by status (found/total): " + ", ".join(f"{k} {a}/{b}" for k, (a, b) in sorted(by.items()))
              + ". Recall here is not trustworthy until NRL posts confirmations for these dates:"
              " then `kreutz reports sync` and re-score.")

    soho = g1_visible_soho(con, start, end)
    def coincident(t):
        days = {datetime.fromisoformat(h["frame_time"]).date() for h in t["hits"]}
        return [lab for lab, a, b in soho if any(a <= d <= b for d in days)]
    fps = [t for t in live if t["id"] not in found_tracks]
    if soho:
        print()
        print("comets on the SOHO list also seen in G1 (positions in LASCO pixels; matched by date only):")
        for lab, a, b in soho:
            n = sum(1 for t in fps if lab in coincident(t))
            print(f"  {lab}: images {a}..{b}; {n} unmatched tracks fall in those dates")
        print()
        print(f"{'threshold':<10} {'precision excluding tracks on those dates':>42}")
        for k in THRESHOLDS:
            at_k = [t for t in tracks if live_at(t, k) and not (t["id"] not in found_tracks and coincident(t))]
            tp = [t for t in at_k if t["id"] in found_tracks]
            print(f"{k:>3} hits   {len(tp)}/{len(at_k)} = {len(tp) / len(at_k) if at_k else float('nan'):.2f}")
            summary[k]["precision_excl_soho_dates"] = len(tp) / len(at_k) if at_k else float("nan")
    print()
    print(f"unmatched tracks (false positives unless a human missed it): {len(fps)}")
    outdir = A.derived_dir(camera) / f"sheets_{start}_{end}"
    keep = results / "sheets"
    (results / "tracks.json").write_text(json.dumps(live, indent=1))
    if sheets:
        import shutil

        from . import contact

        if keep.exists():
            shutil.rmtree(keep)  # results reflect this run only (git keeps history)
        keep.mkdir(parents=True)
        sheet_list = [(f"fp_{t['id']}", t, "5" in t["reached"]) for t in fps] + \
                     [(f"tp_{tr.label.replace(' ', '_')}_{t['id']}", t, True) for tr, m, _ in rows for t in m[:1]]
        for name, t, publish in sheet_list:
            contact.contact_sheet(t, outdir / f"{name}.png")
            if publish:
                for ext in (".png", ".txt"):
                    if (outdir / f"{name}{ext}").exists():
                        shutil.copy(outdir / f"{name}{ext}", keep / f"{name}{ext}")
    for t in sorted(fps, key=lambda t: -max(h["sig"] for h in t["hits"])):
        best = max(h["sig"] for h in t["hits"])
        h0 = t["hits"][0]
        near = [o for o in others if matches(t, o)]
        note = ("; also reported by humans: " + ", ".join(f"{o.label} ({o.status})" for o in near)) if near else ""
        if coincident(t):
            note += f"; during {', '.join(coincident(t))}"
        print(f"  track {t['id']:>6}: {len(t['hits'])} hits, best sig {best:5.1f}, first {h0['frame_time'][5:16]} "
              f"at ({h0['x']:.0f},{h0['y']:.0f}), v=({t['velocity'][0]:+.1f},{t['velocity'][1]:+.1f})"
              f"{note}" + (f"  -> {_rel(keep if '5' in t['reached'] else outdir)}/fp_{t['id']}.png" if sheets else ""))
    return summary


def _rel(p):
    from .paths import ROOT

    try:
        return p.relative_to(ROOT)
    except ValueError:
        return p


def _count_rejections(tracks):
    out = {}
    for t in tracks:
        if t["status"].startswith("rejected:"):
            k = t["status"].split(":", 1)[1]
            out[k] = out.get(k, 0) + 1
    return out
