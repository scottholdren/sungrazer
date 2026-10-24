"""SQLite answer key: data/reports.sqlite.

Tables
  reports        one row per Sungrazer report (all times UTC)
  frames         per-frame positions of each report, as typed and in our convention
  confirmations  rows from the admins' confirmation lists, linked to reports

Status values (derived on every sync, never typed by hand):
  confirmed  report appears in a confirmation list
  retracted  the reporter posted a "Retracting Report" for it
  rejected   a list covering that period exists for the camera and it isn't on it
  known      "Known Comet Now Entering" (a re-sighting, not a discovery claim)
  reported   none of the above yet
Follow-ups ("Additional Observations", "Correction", "Retracting Report") point at
the report they reference via parent_id and inherit its status, unless the
follow-up was itself confirmed.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .. import cameras, coords, paths
from .parse import Confirmation, Report

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    report_id        TEXT PRIMARY KEY,
    reporter         TEXT,
    report_timestamp TEXT,      -- posting time, ISO UTC
    image_date       TEXT,      -- date of first frame
    camera           TEXT,      -- our id: ccor1 / ccor2 / c2 / c3
    sighting_type    TEXT,
    comet_group      TEXT,
    image_size       TEXT,
    origin           TEXT,      -- Sungrazer (0,0) choice as given, e.g. "Upper Left"
    time_referenced  TEXT,      -- for follow-ups: posting time of referenced report (UTC, minute)
    parent_id        TEXT,
    endorsements     TEXT,
    status           TEXT NOT NULL DEFAULT 'reported',
    frames_text      TEXT,      -- raw position block exactly as posted
    page_fetched     INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT
);
CREATE TABLE IF NOT EXISTS frames (
    report_id  TEXT NOT NULL REFERENCES reports(report_id),
    seq        INTEGER NOT NULL,
    frame_time TEXT,            -- ISO UTC, minute precision as reported
    x          REAL,            -- as typed by the hunter
    y          REAL,
    x_ul       REAL,            -- converted to our report coordinates (upper-left origin)
    y_ul       REAL,
    raw        TEXT NOT NULL,
    PRIMARY KEY (report_id, seq)
);
CREATE TABLE IF NOT EXISTS confirmations (
    series      TEXT NOT NULL,  -- CCR1, SOHO, ...
    number      TEXT NOT NULL,
    post_time   TEXT NOT NULL,  -- ISO UTC
    discoverer  TEXT,
    telescopes  TEXT,
    comet_group TEXT,
    images_of   TEXT,
    source      TEXT,
    report_id   TEXT,
    PRIMARY KEY (series, number)
);
CREATE INDEX IF NOT EXISTS reports_cam_time ON reports(camera, report_timestamp);
"""

# Confirmation-list series -> our camera id
# (the first CCOR-1 list, March 2025, used the header "CCOR#")
SERIES_CAMERA = {"CCOR": "ccor1", "CCR1": "ccor1", "CCR2": "ccor2"}


def connect(path=None) -> sqlite3.Connection:
    path = path or paths.REPORTS_DB
    if str(path) != ":memory:":
        paths.DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def camera_id(sungrazer_label: str | None) -> str | None:
    if not sungrazer_label:
        return None
    return cameras.BY_SUNGRAZER_NAME.get(sungrazer_label, sungrazer_label.lower())


def _iso(t: datetime | date | None) -> str | None:
    return t.isoformat() if t else None


def image_shape(image_size: str | None) -> tuple[int, int]:
    """(width, height) from e.g. "2048x1920 (CCOR1)"; CCOR size if unknown."""
    m = re.match(r"\s*(\d+)\s*x\s*(\d+)", image_size or "")
    if m:
        return int(m.group(1)), int(m.group(2))
    return coords.CCOR_WIDTH, coords.CCOR_HEIGHT


def upsert_report(con, r: Report, from_page: bool) -> None:
    """Insert or update. Listing rows carry endorsements; page rows carry origin etc."""
    cam = camera_id(r.camera)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con.execute(
        """INSERT INTO reports (report_id, reporter, report_timestamp, image_date, camera,
               sighting_type, comet_group, image_size, origin, time_referenced, endorsements,
               frames_text, page_fetched, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(report_id) DO UPDATE SET
               reporter = COALESCE(excluded.reporter, reporter),
               image_date = COALESCE(excluded.image_date, image_date),
               camera = COALESCE(excluded.camera, camera),
               sighting_type = COALESCE(excluded.sighting_type, sighting_type),
               comet_group = COALESCE(excluded.comet_group, comet_group),
               image_size = COALESCE(excluded.image_size, image_size),
               origin = COALESCE(excluded.origin, origin),
               time_referenced = COALESCE(excluded.time_referenced, time_referenced),
               endorsements = COALESCE(excluded.endorsements, endorsements),
               frames_text = CASE WHEN excluded.frames_text <> '' THEN excluded.frames_text ELSE frames_text END,
               page_fetched = MAX(page_fetched, excluded.page_fetched),
               updated_at = excluded.updated_at""",
        (r.report_id, r.reporter, _iso(r.report_timestamp), _iso(r.image_date), cam,
         r.sighting_type, r.comet_group, r.image_size, r.origin, _iso(r.time_referenced),
         ", ".join(r.endorsements) if not from_page else None,
         r.frames_text, int(from_page), now))
    if r.frames:
        write_frames(con, r.report_id)


def write_frames(con, report_id: str) -> None:
    """(Re)build frame rows from the stored raw text, origin and image date."""
    from .parse import parse_frames

    row = con.execute("SELECT * FROM reports WHERE report_id=?", (report_id,)).fetchone()
    d = date.fromisoformat(row["image_date"]) if row["image_date"] else None
    w, h = image_shape(row["image_size"])
    con.execute("DELETE FROM frames WHERE report_id=?", (report_id,))
    for f in parse_frames(row["frames_text"] or "", d):
        xu = yu = None
        if f.x is not None:
            try:
                xu, yu = coords.normalize_origin(f.x, f.y, row["origin"], h, w)
            except ValueError:
                pass
        con.execute("INSERT INTO frames VALUES (?,?,?,?,?,?,?,?)",
                    (report_id, f.seq, _iso(f.frame_time), f.x, f.y, xu, yu, f.raw))


def upsert_confirmations(con, confs: list[Confirmation], source: str) -> None:
    for c in confs:
        con.execute(
            """INSERT INTO confirmations (series, number, post_time, discoverer, telescopes,
                   comet_group, images_of, source)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(series, number) DO UPDATE SET
                   post_time=excluded.post_time, discoverer=excluded.discoverer,
                   telescopes=excluded.telescopes, comet_group=excluded.comet_group,
                   images_of=excluded.images_of, source=excluded.source""",
            (c.series, c.number, _iso(c.post_time), c.discoverer, c.telescopes,
             c.comet_group, c.images_of, source))


def recompute_status(con) -> None:
    """Derive parent links and statuses from scratch. Safe to run any number of times."""
    reports = {r["report_id"]: dict(r) for r in con.execute("SELECT * FROM reports")}
    by_minute: dict[str, list[dict]] = {}
    for r in reports.values():
        by_minute.setdefault(r["report_timestamp"][:16], []).append(r)

    # 1. Follow-ups -> parent, by the referenced posting minute.
    for r in reports.values():
        r["parent_id"] = None
        if r["time_referenced"]:
            cands = [c for c in by_minute.get(r["time_referenced"][:16], []) if c["report_id"] != r["report_id"]]
            cands.sort(key=lambda c: (c["reporter"] != r["reporter"], c["camera"] != r["camera"]))
            if cands:
                r["parent_id"] = cands[0]["report_id"]

    # 2. Confirmations -> report, by exact posting second (+-2 s for safety).
    by_second = {}
    for r in reports.values():
        by_second.setdefault(r["report_timestamp"][:19], []).append(r)
    confirmed, coverage = set(), {}
    for c in con.execute("SELECT * FROM confirmations"):
        t = datetime.fromisoformat(c["post_time"])
        match = None
        for dt in (0, -1, 1, -2, 2):
            key = (t + timedelta(seconds=dt)).isoformat()[:19]
            cands = [x for x in by_second.get(key, []) if x["sighting_type"] not in ("Retracting Report",)]
            if cands:
                match = cands[0]["report_id"]
                break
        con.execute("UPDATE confirmations SET report_id=? WHERE series=? AND number=?",
                    (match, c["series"], c["number"]))
        if match:
            confirmed.add(match)
        cam = SERIES_CAMERA.get(c["series"])
        if cam:
            coverage[cam] = max(coverage.get(cam, ""), c["post_time"])

    retracted = {r["parent_id"] for r in reports.values()
                 if r["sighting_type"] == "Retracting Report" and r["parent_id"]}

    def own_status(r):
        if r["report_id"] in confirmed:
            return "confirmed"
        if r["report_id"] in retracted:
            return "retracted"
        if r["sighting_type"] == "Known Comet Now Entering":
            return "known"
        if r["sighting_type"] in ("Potential Comet", "Possible Fragment/Companion"):
            if r["camera"] in coverage and r["report_timestamp"] <= coverage[r["camera"]]:
                return "rejected"
        return "reported"

    for r in reports.values():
        status = own_status(r)
        # A follow-up inherits its parent's fate -- unless the admins confirmed the
        # follow-up itself (it happens: a hunter's "additional observations" can
        # turn out to be the real comet while the original was noise).
        if (status != "confirmed" and r["parent_id"]
                and r["sighting_type"] in ("Additional Observations", "Correction", "Retracting Report")):
            status = own_status(reports[r["parent_id"]])
        con.execute("UPDATE reports SET parent_id=?, status=? WHERE report_id=?",
                    (r["parent_id"], status, r["report_id"]))


# --- queries -----------------------------------------------------------------------

def list_reports(camera=None, start: date | None = None, end: date | None = None, con=None):
    """Reports whose first frame falls in [start, end] (dates inclusive)."""
    con = con or connect()
    q = ["SELECT r.*, COUNT(f.seq) AS n_frames, MIN(f.frame_time) AS first_frame, "
         "MAX(f.frame_time) AS last_frame FROM reports r LEFT JOIN frames f USING (report_id) WHERE 1=1"]
    args = []
    if camera:
        q.append("AND r.camera = ?"); args.append(camera)
    if start:
        q.append("AND r.image_date >= ?"); args.append(start.isoformat())
    if end:
        q.append("AND r.image_date <= ?"); args.append(end.isoformat())
    q.append("GROUP BY r.report_id ORDER BY r.report_timestamp")
    return con.execute(" ".join(q), args).fetchall()


def print_table(rows) -> None:
    cols = [("report_id", 24), ("reporter", 18), ("posted (UTC)", 16), ("cam", 5),
            ("type", 22), ("group", 9), ("n", 3), ("first frame", 16), ("last frame", 16), ("status", 9)]
    print("  ".join(name.ljust(w) for name, w in cols))
    print("  ".join("-" * w for _, w in cols))
    for r in rows:
        vals = [r["report_id"], r["reporter"] or "", (r["report_timestamp"] or "")[:16].replace("T", " "),
                r["camera"] or "", r["sighting_type"] or "", r["comet_group"] or "", str(r["n_frames"]),
                (r["first_frame"] or "")[:16].replace("T", " "), (r["last_frame"] or "")[:16].replace("T", " "),
                r["status"]]
        print("  ".join(str(v)[:w].ljust(w) for v, (_, w) in zip(vals, cols)))
    print(f"{len(rows)} reports")
