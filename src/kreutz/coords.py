"""The one pixel-coordinate convention used everywhere in this project.

REPORT COORDINATES (x, y)  -- what we store, compare, and submit:
  * origin (0, 0) at the UPPER-LEFT corner of the image as it is displayed
    (Sun-north up, the way SWPC JPEGs and Sungrazer hunters see it);
  * x increases to the right, y increases DOWNWARD;
  * 0-based, integer values are pixel centres;
  * this equals Sungrazer's "Upper Left" (0,0) option.

How that relates to the FITS array as astropy loads it (data[row, col]):
  FITS stores the bottom row of the displayed image first, so row 0 is the
  BOTTOM. Therefore
        x = col
        y = (height - 1) - row
  and FITS 1-based pixel numbers (CRPIX, ds9) are col = x_fits - 1, row = y_fits - 1.

Some hunters choose another corner ("Lower Left", "Upper/Lower Right"); those
are mirrored into this convention on import (normalize_origin).
Verified against confirmed comet reports by scripts/verify_coords.py; tested in tests/test_coords.py.
Do not change this without updating the README and the tests.
"""

from __future__ import annotations

CCOR_HEIGHT = 1920
CCOR_WIDTH = 2048


def fits_to_report(col: float, row: float, height: int = CCOR_HEIGHT) -> tuple[float, float]:
    """numpy (col, row) of a FITS image -> report (x, y)."""
    return col, (height - 1) - row


def report_to_fits(x: float, y: float, height: int = CCOR_HEIGHT) -> tuple[float, float]:
    """report (x, y) -> numpy (col, row); index the array as data[row, col]."""
    return x, (height - 1) - y


def normalize_origin(x: float, y: float, origin: str | None,
                     height: int = CCOR_HEIGHT, width: int = CCOR_WIDTH) -> tuple[float, float]:
    """Convert a position quoted with a Sungrazer origin choice to report coordinates.

    The form offers Upper/Lower Left/Right; a missing origin is taken as Upper Left.
    """
    o = " ".join((origin or "upper left").lower().split())
    if o not in ("upper left", "lower left", "upper right", "lower right"):
        raise ValueError(f"unknown origin {origin!r}")
    if o.endswith("right"):
        x = (width - 1) - x
    if o.startswith("lower"):
        y = (height - 1) - y
    return x, y
