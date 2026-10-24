from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from kreutz.reports import db, parse

UTC = timezone.utc
FIX = Path(__file__).parent / "fixtures"


# --- frame text ------------------------------------------------------------------

def test_frames_basic():
    fr = parse.parse_frames("0800 2021 1375\n0830 2005 1370\n", date(2026, 10, 23))
    assert [(f.frame_time.hour, f.frame_time.minute, f.x, f.y) for f in fr] == [
        (8, 0, 2021, 1375), (8, 30, 2005, 1370)]
    assert fr[0].frame_time.tzinfo == UTC


def test_frames_roll_over_midnight():
    fr = parse.parse_frames("2345 1820 1718\n0030 1806 1707\n0100 1796 1699", date(2026, 10, 22))
    assert [f.frame_time for f in fr] == [
        datetime(2026, 10, 22, 23, 45, tzinfo=UTC),
        datetime(2026, 10, 23, 0, 30, tzinfo=UTC),
        datetime(2026, 10, 23, 1, 0, tzinfo=UTC)]


def test_frames_separator_variants():
    text = "08:00 2021 1375\n08:15, 2013.5, 1372\n0830\t2005,1370\n9:45:25 10 20"
    fr = parse.parse_frames(text, date(2025, 7, 17))
    assert [(f.frame_time.minute, f.x, f.y) for f in fr] == [
        (0, 2021, 1375), (15, 2013.5, 1372), (30, 2005, 1370), (45, 10, 20)]


def test_frames_bad_lines_are_kept_not_dropped():
    fr = parse.parse_frames("0800 2021 1375\ncomet is faint\n2561 1 2\n0815 2013 1372", date(2025, 7, 17))
    assert len(fr) == 4
    assert fr[1].x is None and fr[1].raw == "comet is faint"
    assert fr[2].frame_time is None  # 25:61 is not a time
    assert fr[3].frame_time.minute == 15


# --- timestamps ------------------------------------------------------------------

def test_report_id_offset_is_5h_in_january_4h_in_july():
    for rid, naive, hours in [("report_jr_20250122194256", datetime(2025, 1, 22, 19, 42, 56), 5),
                              ("report_jr_20250717052526", datetime(2025, 7, 17, 5, 25, 26), 4)]:
        utc = parse.id_timestamp(rid)
        assert utc - naive.replace(tzinfo=UTC) == timedelta(hours=hours)


def test_report_id_is_eastern_time():
    # July: EDT = UTC-4
    assert parse.id_timestamp("report_jr_20250717052526") == datetime(2025, 7, 17, 9, 25, 26, tzinfo=UTC)
    # January: EST = UTC-5
    assert parse.id_timestamp("report_jr_20250122194256") == datetime(2025, 1, 23, 0, 42, 56, tzinfo=UTC)
    assert parse.canonical_id("/report/reportjr20250717052526") == "report_jr_20250717052526"


# --- pages -------------------------------------------------------------------------

def test_listing_page():
    rows = parse.parse_listing((FIX / "listing.html").read_text())
    ids = [r.report_id for r in rows]
    assert ids[:4] == ["report_nu_20261024083824", "report_cf_20261024072819",
                       "report_jr_20261024063345", "report_jr_20261024054650"]
    jr = rows[3]
    assert (jr.reporter, jr.camera, jr.comet_group, jr.sighting_type) == (
        "Jiangao Ruan", "CCOR2", "Kreutz", "Potential Comet")
    assert jr.image_date == date(2026, 10, 24)
    assert jr.endorsements == ["Mikolaj Kaszczyk"]
    assert [(f.x, f.y) for f in jr.frames] == [(1688, 1648), (1683, 1643), (1677, 1639)]
    assert rows[0].frames == []  # NEW USER report with no positions


def test_report_page_with_frames():
    r = parse.parse_report_page((FIX / "report_frames.html").read_text(), "report_cw_20261023091620")
    assert r.reporter == "Chris Wage"
    assert r.camera == "CCOR1" and r.image_size == "2048x1920 (CCOR1)"
    assert r.origin == "Upper Left"
    assert len(r.frames) == 12 and (r.frames[-1].x, r.frames[-1].y) == (1926, 1342)
    assert r.frames[-1].frame_time == datetime(2026, 10, 23, 11, 0, tzinfo=UTC)
    # posted 09:16 EDT = 13:16 UTC, after the last 11:00 UT frame
    assert r.report_timestamp > r.frames[-1].frame_time


def test_retraction_page_and_parent_link():
    r = parse.parse_report_page((FIX / "report_retract.html").read_text(), "report_ms_20250717152226")
    assert r.sighting_type == "Retracting Report"
    assert r.time_referenced == datetime(2025, 7, 17, 19, 8, tzinfo=UTC)
    assert r.frames == []

    con = db.connect(":memory:")
    parent = parse.Report("report_ms_20250717150817", reporter="Michael Stark", camera="CCOR1",
                          sighting_type="Potential Comet", image_date=date(2025, 7, 17),
                          report_timestamp=parse.id_timestamp("report_ms_20250717150817"),
                          frames_text="1515 100 200\n1530 110 210")
    db.upsert_report(con, parent, from_page=False)
    db.upsert_report(con, r, from_page=True)
    db.recompute_status(con)
    st = dict(con.execute("SELECT report_id, status FROM reports").fetchall())
    assert st == {"report_ms_20250717150817": "retracted", "report_ms_20250717152226": "retracted"}
    assert con.execute("SELECT parent_id FROM reports WHERE report_id='report_ms_20250717152226'"
                       ).fetchone()[0] == "report_ms_20250717150817"


def test_resync_updates_without_duplicating():
    con = db.connect(":memory:")
    rows = parse.parse_listing((FIX / "listing.html").read_text())
    for _ in range(2):
        for r in rows:
            db.upsert_report(con, r, from_page=False)
    assert con.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == len(rows)
    assert con.execute("SELECT COUNT(*) FROM frames WHERE report_id='report_jr_20261024054650'").fetchone()[0] == 3


# --- confirmations ------------------------------------------------------------------

CONF_TEXT = """
CCR1# Date/Time of Post Discoverer Tel Group Images of... Chat#
=====================================================================
0073 Jul17,25 05:25:26 J.Ruan G1 Kreutz Jul17,25
0076 Jul19,25 22:05:12 R.Pickard G1 Kreutz Jul20,25
0080 Jul24,25 09:21:12 M.Stark G1,C3 Kreutz Jul24-25.26
---
There was a report from Jiangao on Jul17,25 03:08:31 that I couldn't see.
"""


def test_confirmation_table():
    cs = parse.parse_confirmation_text(CONF_TEXT)
    assert [c.number for c in cs] == ["0073", "0076", "0080"]
    assert cs[0].series == "CCR1" and cs[0].discoverer == "J.Ruan"
    # list times are the same Eastern clock as report ids
    assert cs[1].post_time == parse.id_timestamp("report_rp_20250719220512")
    assert cs[2].telescopes == "G1,C3"


def test_confirmed_followup_not_overridden_by_rejected_parent():
    """Jul 17 2025: the first report was noise, the follow-up was the real comet (CCR1 0073)."""
    con = db.connect(":memory:")
    first = parse.Report("report_jr_20250717030831", reporter="Jiangao Ruan", camera="CCOR1",
                         sighting_type="Potential Comet",
                         report_timestamp=parse.id_timestamp("report_jr_20250717030831"))
    follow = parse.Report("report_jr_20250717052526", reporter="Jiangao Ruan", camera="CCOR1",
                          sighting_type="Additional Observations",
                          report_timestamp=parse.id_timestamp("report_jr_20250717052526"),
                          time_referenced=parse.id_timestamp("report_jr_20250717030831").replace(second=0))
    for r in (first, follow):
        db.upsert_report(con, r, from_page=True)
    db.upsert_confirmations(con, parse.parse_confirmation_text(CONF_TEXT), "test")
    db.recompute_status(con)
    st = dict(con.execute("SELECT report_id, status FROM reports").fetchall())
    assert st == {"report_jr_20250717030831": "rejected", "report_jr_20250717052526": "confirmed"}


def test_images_of_ranges():
    from kreutz.score import images_of_range

    assert images_of_range("Jul23-25,25") == (date(2025, 7, 23), date(2025, 7, 25))
    assert images_of_range("Jul31-Aug01,25") == (date(2025, 7, 31), date(2025, 8, 1))
    assert images_of_range("Jul08,25") == (date(2025, 7, 8), date(2025, 7, 8))
    assert images_of_range("Jul24-25.26", year=2025) == (date(2025, 7, 24), date(2025, 7, 25))
    assert images_of_range("") is None
