"""The coordinate convention (see kreutz/coords.py and the README). Never change silently."""

import numpy as np
import pytest

from kreutz import coords
from kreutz.inspect import stretch


def test_corners():
    H = 1920
    # bottom-left pixel of the FITS array is the bottom-left of the displayed image
    assert coords.fits_to_report(0, 0, H) == (0, H - 1)
    # last FITS row is the TOP of the displayed image -> y = 0
    assert coords.fits_to_report(2047, H - 1, H) == (2047, 0)


def test_round_trip():
    rng = np.random.default_rng(1)
    for col, row in rng.uniform(0, 1919, size=(50, 2)):
        x, y = coords.fits_to_report(col, row)
        assert coords.report_to_fits(x, y) == pytest.approx((col, row))


def test_report_position_indexes_the_right_pixel():
    data = np.zeros((1920, 2048), dtype=np.float32)
    x, y = 1500, 300  # near the top-right as a hunter sees it
    col, row = coords.report_to_fits(x, y)
    data[int(row), int(col)] = 1.0
    assert data[1619, 1500] == 1.0  # FITS row 1620 (1-based) -> high up in the display


def test_preview_png_uses_report_orientation():
    """Pixel (x, y) of the PNG preview is report (x, y)."""
    data = np.ones((1920, 2048), dtype=np.float32)
    col, row = coords.report_to_fits(1500, 300)
    data[int(row), int(col)] = 50.0
    img = stretch(data)
    assert img[300, 1500] == img.max()


def test_lower_left_origin_is_converted():
    assert coords.normalize_origin(100, 200, "Upper Left") == (100, 200)
    assert coords.normalize_origin(100, 200, "Lower Left") == (100, 1719)
    assert coords.normalize_origin(100, 200, None) == (100, 200)
    assert coords.normalize_origin(100, 200, "Upper Right") == (1947, 200)
    assert coords.normalize_origin(100, 200, "Lower Right") == (1947, 1719)
    with pytest.raises(ValueError):
        coords.normalize_origin(1, 2, "sideways")


def test_sun_centre_in_report_coords():
    """On a real frame the Sun (helioprojective 0,0) sits near the middle in report coords."""
    import warnings

    from astropy.wcs import WCS

    from kreutz import fetch
    from kreutz.fitsio import read_frame

    paths = fetch.local_frames("ccor1")
    if not paths:
        pytest.skip("no frames downloaded")
    f = read_frame(paths[0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        col, row = (float(v) for v in WCS(f.header).world_to_pixel_values(0, 0))
    x, y = coords.fits_to_report(col, row, f.data.shape[0])
    assert 900 < x < 1150 and 850 < y < 1050
    # The occulter covers the Sun: the pixel there is blank/NaN or very dark.
    v = f.data[int(round(row)), int(round(col))]
    assert not np.isfinite(v) or v < np.nanpercentile(f.data, 5)


# --- orientation guard ---------------------------------------------------------------

def _header(**kw):
    from astropy.io import fits

    h = fits.Header()
    h["INSTRUME"] = "CCOR1"
    h["L2RFLIP"], h["NROT90"], h["YAWFLIP"] = True, 0, 2
    for k, v in kw.items():
        h[k] = v
    return h


def test_verified_orientation_passes():
    assert coords.check_orientation(_header()) == ("ccor1", True, 0, 2)
    # post-yaw-flip state seen from Oct 2026, verified separately
    assert coords.check_orientation(_header(NROT90=2, YAWFLIP=0)) == ("ccor1", True, 2, 0)


@pytest.mark.parametrize("change", [{"NROT90": 2}, {"YAWFLIP": 0}, {"L2RFLIP": False}, {"INSTRUME": "CCOR2"}])
def test_changed_orientation_raises(change):
    with pytest.raises(coords.OrientationChanged):
        coords.check_orientation(_header(**change))


def test_missing_flags_raise():
    h = _header()
    del h["YAWFLIP"]
    with pytest.raises(coords.OrientationChanged):
        coords.check_orientation(h)


def test_read_frame_enforces_guard(tmp_path):
    from astropy.io import fits

    from kreutz.fitsio import read_frame

    p = tmp_path / "CCOR1_1B_20261024T151525_V00_NC.fits"
    hdr = _header(NROT90=1, YAWFLIP=0)  # a combination never verified
    hdr["DATE-OBS"] = "2026-10-24T15:15:25.000"
    fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(np.zeros((4, 4), np.float32), hdr)]).writeto(p)
    with pytest.raises(coords.OrientationChanged):
        read_frame(p)
    assert read_frame(p, check_orientation=False).data.shape == (4, 4)
