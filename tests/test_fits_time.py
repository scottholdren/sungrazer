from datetime import datetime, timezone

import pytest
from astropy.io import fits

from kreutz.fitsio import filename_time, mid_time, obs_time, parse_fits_time

UTC = timezone.utc


@pytest.mark.parametrize("s, expected", [
    ("2025-07-17T12:00:25.701", datetime(2025, 7, 17, 12, 0, 25, 701000, UTC)),
    ("2025-07-17T12:00:25", datetime(2025, 7, 17, 12, 0, 25, tzinfo=UTC)),
    ("2025-07-17T12:00:25.7Z", datetime(2025, 7, 17, 12, 0, 25, 700000, UTC)),
    ("2025-07-17 12:00:25.70123456", datetime(2025, 7, 17, 12, 0, 25, 701234, UTC)),
    ("  2025-07-17T23:59:59.999 ", datetime(2025, 7, 17, 23, 59, 59, 999000, UTC)),
])
def test_parse_fits_time(s, expected):
    assert parse_fits_time(s) == expected


def test_filename_time():
    assert filename_time("CCOR1_1B_20250717T120025_V00_NC.fits") == datetime(2025, 7, 17, 12, 0, 25, tzinfo=UTC)
    assert filename_time("/x/y/CCOR2_1A_20261020T000014_V00_NC.fits") == datetime(2026, 10, 20, 0, 0, 14, tzinfo=UTC)
    with pytest.raises(ValueError):
        filename_time("nothing.fits")


def test_obs_time_prefers_date_obs_then_beg_then_filename():
    h = fits.Header()
    h["DATE-OBS"] = "2025-07-17T12:00:25.701"
    h["DATE-BEG"] = "2025-07-17T12:00:20.000"
    h["DATE-AVG"] = "2025-07-17T12:00:39.680"
    assert obs_time(h) == datetime(2025, 7, 17, 12, 0, 25, 701000, UTC)
    assert mid_time(h) == datetime(2025, 7, 17, 12, 0, 39, 680000, UTC)

    del h["DATE-OBS"]
    assert obs_time(h).second == 20

    empty = fits.Header()
    assert obs_time(empty, "CCOR1_1B_20250717T120025_V00_NC.fits").minute == 0
    assert mid_time(empty, "CCOR1_1B_20250717T120025_V00_NC.fits").second == 25
    with pytest.raises(ValueError):
        obs_time(empty)


def test_filename_matches_header_on_real_frame():
    """DATE-OBS and the filename agree to the second on a real archive frame."""
    from kreutz import fetch
    from kreutz.fitsio import read_frame

    paths = fetch.local_frames("ccor1")
    if not paths:
        pytest.skip("no frames downloaded")
    f = read_frame(paths[0])
    assert abs((f.time - filename_time(paths[0].name)).total_seconds()) < 1
    assert f.data.shape == (1920, 2048)
