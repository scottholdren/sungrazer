from datetime import date, datetime, timezone

import pytest

from kreutz import frameindex, paths
from kreutz.reports import db, parse

UTC = timezone.utc


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "FRAMES", tmp_path)
    d = tmp_path / "ccor1" / "20250717"
    d.mkdir(parents=True)
    for hhmm in ("1200", "1215", "1245"):  # 12:30 missing
        (d / f"CCOR1_1B_20250717T{hhmm}25_V00_NC.fits").touch()
    return frameindex.FrameIndex("ccor1")


def test_nearest_within_two_minutes(index):
    assert index.paths[index.nearest(datetime(2025, 7, 17, 12, 15, tzinfo=UTC))].name.startswith("CCOR1_1B_20250717T1215")
    assert index.nearest(datetime(2025, 7, 17, 12, 0, tzinfo=UTC)) == 0
    assert index.nearest(datetime(2025, 7, 17, 12, 47, 20, tzinfo=UTC)) == 2  # 1m55s away


def test_no_match_beyond_two_minutes(index):
    assert index.nearest(datetime(2025, 7, 17, 12, 30, tzinfo=UTC)) is None  # gap in data
    assert index.nearest(datetime(2025, 7, 17, 11, 50, tzinfo=UTC)) is None
    assert index.nearest(datetime(2025, 7, 18, 12, 0, tzinfo=UTC)) is None


def test_match_report_requires_same_camera(index):
    con = db.connect(":memory:")
    text = "1200 10 20\n1215 12 22\n1230 14 24"
    for rid, cam in (("report_ms_20250717090000", "CCOR1"), ("report_ms_20250717090100", "CCOR2")):
        db.upsert_report(con, parse.Report(rid, camera=cam, sighting_type="Potential Comet",
                                           image_date=date(2025, 7, 17),
                                           report_timestamp=parse.id_timestamp(rid), frames_text=text,
                                           frames=parse.parse_frames(text, date(2025, 7, 17))),
                         from_page=True)
    m = frameindex.match_report(con, "report_ms_20250717090000", index)
    assert [i for _, i in m] == [0, 1, None]
    with pytest.raises(ValueError):
        frameindex.match_report(con, "report_ms_20250717090100", index)
