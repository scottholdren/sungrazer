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

ORIENTATION GUARD
The ground pipeline may flip/rotate the image before writing it (it records
what it did in L2RFLIP, NROT90; YAWFLIP is the spacecraft's yaw state). The
convention above was verified only for the flag combinations listed in
VERIFIED_ORIENTATIONS. A frame with any other combination raises
OrientationChanged: re-run scripts/verify_coords.py on frames with the new
flags, and add them here (with the evidence) only if it passes.
"""

from __future__ import annotations

CCOR_HEIGHT = 1920
CCOR_WIDTH = 2048

# (camera, L2RFLIP, NROT90, YAWFLIP) -> how it was verified
VERIFIED_ORIENTATIONS: dict[tuple, str] = {
    ("ccor1", True, 0, 2): "2025-07-17..24, 50 positions of 8 confirmed comets; "
                           "stack peak at offset (0,0), 14x scatter",
}


class OrientationChanged(Exception):
    """A frame's orientation flags differ from every combination we verified."""


def orientation_key(header, camera: str | None = None) -> tuple:
    cam = (camera or str(header.get("INSTRUME", "")).strip()).lower()
    return (cam, header.get("L2RFLIP"), header.get("NROT90"), header.get("YAWFLIP"))


def check_orientation(header, camera: str | None = None, source: str = "") -> tuple:
    """Return the flag key, or raise OrientationChanged if it was never verified."""
    key = orientation_key(header, camera)
    if key not in VERIFIED_ORIENTATIONS:
        verified = ", ".join(str(k) for k in VERIFIED_ORIENTATIONS)
        raise OrientationChanged(
            f"{source or 'frame'}: orientation flags (camera, L2RFLIP, NROT90, YAWFLIP) = {key} "
            f"not verified (verified: {verified}). Run scripts/verify_coords.py on these frames.")
    return key


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
