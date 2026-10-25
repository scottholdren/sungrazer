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

REPORTS NOT MEASURED ON THE FULL-RESOLUTION FRAME (report_mapping)
The form's "image size" field is not reliable: 11 confirmed CCOR-1 reports
declare 1024x1024 but quote positions up to x=2032, y=1745, so they were
measured on the 2048x1920 frame (stack test: scripts/verify_report_scale.py).
What decides is the reporter's tool, so the second convention is keyed by
reporter and verified dates (REPORT_TRANSFORMS), not by declared size:
  * declared 2048x1920, or any position beyond 1023 -> this convention;
  * a reporter with a verified transform, on its verified dates -> the transform;
  * otherwise a smaller declared size, or a reporter whose transform isn't
    verified for that date -> unmapped: positions are dropped (x_ul = NULL), so
    they stay out of truth, velocity grids and envelopes.
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

import math
from dataclasses import dataclass
from datetime import date

CCOR_HEIGHT = 1920
CCOR_WIDTH = 2048

# (camera, L2RFLIP, NROT90, YAWFLIP) -> how it was verified
VERIFIED_ORIENTATIONS: dict[tuple, str] = {
    ("ccor1", True, 0, 2): "2025-07-17..24, 50 positions of 8 confirmed comets; "
                           "stack peak at offset (0,0), 14x scatter",
    # After a spacecraft yaw flip the pipeline rotates the image 180 deg (NROT90=2)
    # so solar north stays up; the occulter arm moves from the top to the bottom.
    ("ccor1", True, 2, 0): "2026-10-17..24, 30 positions of unreviewed (non-retracted) "
                           "hunter reports; stack peak at offset (row 0, col -1), 13x scatter",
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


@dataclass(frozen=True)
class ReportTransform:
    """Report (x, y) = scale * R(rot) * (typed x, y) + offset, rotation in the y-up sense.

    The typed numbers are used as typed: the transform absorbs the origin choice."""
    reporter: str
    start: date  # image dates it was verified on, inclusive
    end: date
    scale: float
    rot_deg: float
    offset: tuple[float, float]
    evidence: str

    def apply(self, x: float, y: float) -> tuple[float, float]:
        c, s = math.cos(math.radians(self.rot_deg)), math.sin(math.radians(self.rot_deg))
        return (self.scale * (x * c + y * s) + self.offset[0],
                self.scale * (-x * s + y * c) + self.offset[1])


REPORT_TRANSFORMS = [
    # Declares 1024x1024 / Lower Right, but the numbers grow right and down like an
    # upper-left origin, and the view is turned to solar north up (rotation ~ -CROTA,
    # -25.5 deg that week) at ~2.2x reduction, Sun at (610, 618) in his image.
    ReportTransform("Bill Collins", date(2026, 10, 17), date(2026, 10, 24), 2.208, 25.0, (-790.7, 288.0),
                    "fitted to tracks 18954 (Oct 19) and 71192 (Oct 23 = cw's comet), 8 positions, "
                    "rms 3.5 px; fitting either comet alone predicts the other to 5-14 px"),
]


def report_mapping(reporter: str | None, image_size: tuple[int, int] | None, image_date: date | None,
                   xs, ys) -> tuple[str, ReportTransform | None]:
    """How a report's typed positions map to report coordinates: ("full" | "transform" | "unmapped", t)."""
    mine = [t for t in REPORT_TRANSFORMS if t.reporter == reporter]
    for t in mine:
        if image_date and t.start <= image_date <= t.end:
            return "transform", t
    if image_size is None or tuple(image_size) == (CCOR_WIDTH, CCOR_HEIGHT):
        return ("unmapped", None) if mine else ("full", None)
    if any(v is not None and v > 1023 for v in list(xs) + list(ys)):
        return "full", None  # the declared size is the form's default; the numbers can't fit it
    return "unmapped", None
