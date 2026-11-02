"""Cross-camera positions: a report on one CCOR camera lands on the same solar
position, and so on the other camera's pixels, through each frame's WCS."""

from datetime import datetime, timezone

import numpy as np
import pytest
from astropy.io import fits

from kreutz import paths, score, solar
from kreutz.reports import db

# WCS of real frames from 2026-11-02 10:00 UTC (CCOR-1 on GOES-19, CCOR-2 on SWFO-L1)
C1 = dict(CRPIX1=1046.9281, CRPIX2=952.9415, CRVAL1=0.170631, CRVAL2=-0.182693, CDELT1=0.005370007, CDELT2=0.005370007,
          PC1_1=0.916776, PC1_2=0.399401, PC2_1=-0.399401, PC2_2=0.916776, RSUN_ARC=966.5866, INSTRUME="CCOR1")
C2 = dict(CRPIX1=1019.4892, CRPIX2=926.0243, CRVAL1=0.076415, CRVAL2=-0.065405, CDELT1=0.006742614, CDELT2=0.006742614,
          PC1_1=0.995257, PC1_2=0.097275, PC2_1=-0.097275, PC2_2=0.995257, RSUN_ARC=976.5732, INSTRUME="CCOR2")
T = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


def _frame(tmp_path, camera, keys, name):
    h = fits.Header()
    for k, v in keys.items():
        h[k] = v
    h["CTYPE1"], h["CTYPE2"], h["CUNIT1"], h["CUNIT2"] = "HPLN-TAN", "HPLT-TAN", "deg", "deg"
    p = paths.frame_dir(camera, name[9:17]) / name
    p.parent.mkdir(parents=True, exist_ok=True)
    fits.PrimaryHDU(np.zeros((1920, 2048), np.float32), header=h).writeto(p)
    return p


@pytest.fixture
def frames(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", tmp_path)
    monkeypatch.setattr(paths, "FRAMES", tmp_path / "frames")
    _frame(tmp_path, "ccor1", C1, "CCOR1_1B_20261102T100025_V00_NC.fits")
    _frame(tmp_path, "ccor2", C2, "CCOR2_1A_20261102T100014_V00_NC.fits")
    solar._wcs.cache_clear()
    return tmp_path


def test_round_trip_and_cross_camera_agreement(frames):
    # our CCOR-1 comet extrapolated to 10:00 and Zesheng Yang's CCOR-2 report of it at 10:00
    rx, ry = solar.to_solar("ccor1", T, 1786, 1540)
    assert np.hypot(rx, ry) == pytest.approx(19.6, abs=0.2)
    x, y = solar.from_solar("ccor1", T, rx, ry)
    assert (x, y) == pytest.approx((1786, 1540), abs=0.05)
    qx, qy = solar.to_solar("ccor2", T, 1457, 1625)
    assert np.hypot(qx - rx, qy - ry) < 0.4  # the same comet from the two spacecraft, in solar radii
    t, xs, ys = solar.cross_positions("ccor2", "ccor1", [T.timestamp()], [1457], [1625])
    assert len(t) == 1 and np.hypot(xs[0] - 1786, ys[0] - 1540) < score.CROSS_MATCH_PX


def test_cross_positions_skips_what_cannot_be_placed(frames):
    # no frame near the time: nothing comes back rather than an exception; a position off the field is dropped
    t, xs, ys = solar.cross_positions("ccor2", "ccor1", [T.timestamp() - 5 * 86400], [1457], [1625], fetch_missing=False)
    assert len(t) == 0
    far = solar.to_solar("ccor2", T, 2040, 10)  # a CCOR-2 corner: outside CCOR-1's narrower field
    t, xs, ys = solar.cross_positions("ccor2", "ccor1", [T.timestamp()], [2040], [10])
    assert len(t) == 0 and np.hypot(*far) > 22


def test_other_reports_includes_the_other_cameras_report_in_our_pixels(frames):
    from kreutz.reports.parse import Report

    con = db.connect(":memory:")
    r = Report(report_id="report_zy_20261102060757", reporter="Zesheng Yang", report_timestamp=datetime(2026, 11, 2, 11, 7, tzinfo=timezone.utc),
               image_date=T.date(), camera="CCOR2", sighting_type="Potential Comet", comet_group="Kreutz",
               image_size="2048x1920 (CCOR1)", origin="Upper Left", time_referenced=None, endorsements=[],
               frames_text="1000 1457 1625\n1015 1452 1621", frames=[])
    db.upsert_report(con, r, from_page=True)
    db.write_frames(con, r.report_id)
    truths = score.other_reports(con, "ccor1", T.date(), T.date())
    assert [t.camera for t in truths] == ["ccor2"] and truths[0].tolerance == score.CROSS_MATCH_PX
    track = {"hits": [{"frame_time": "2026-11-02T10:00:25+00:00", "x": 1786.0, "y": 1540.0},
                      {"frame_time": "2026-11-02T10:15:25+00:00", "x": 1778.5, "y": 1536.0}]}
    assert score.matches(track, truths[0])
    assert not score.matches({"hits": [{"frame_time": "2026-11-02T10:00:25+00:00", "x": 1500.0, "y": 1300.0}]}, truths[0])
