"""Where Kreutz comets come from in a given month, learned from the answer key.

All Kreutz comets share (nearly) one orbit, so on a given date they all
approach the Sun from the same side of the image along nearly parallel paths.
The side rotates through the year: Earth's viewpoint on the orbit changes, and
CCOR-1 images keep celestial north up while the Sun's axis tilts by up to
+-26 deg (the solar P angle). Confirmed CCOR-1 Kreutz tracks show it:

    Mar 2025  enter at position angle ~-170 deg, moving ~12 deg  (left of the Sun, moving right)
    Jul 2025                          ~-38 deg,  moving ~131 deg (lower right, moving up-left)
    Oct 2025 (claims)                 ~-21 deg,  moving ~163 deg

Angles are in report coordinates with y flipped up: position angle (PA) =
direction from the Sun to the object, 0 = right (+x), 90 = up; motion
direction the same way. Same calendar month -> same geometry every year, and
the Oct 2026 yaw flip didn't change it (claims of Oct 2025 and Oct 2026 agree),
so samples are pooled by calendar month across years.

The paths don't aim exactly at the Sun centre (they miss it by 100-200 px in
the image), so PA changes along a path, by up to ~30 deg near the occulter. A
path is therefore described by two things that stay constant along it: its
direction of motion, and its signed miss distance (perpendicular distance from
the Sun to the line, positive when the Sun is to the left of the motion). The
"entry edge" follows from these.

For each month we take the confirmed Kreutz reports of that month (adding the
neighbouring months if there are fewer than MIN_SAMPLES) and allow their
direction range plus ANGLE_MARGIN, their miss-distance range plus MISS_MARGIN,
and their velocity box plus V_MARGIN as the shift-and-stack grid. NRL has published no confirmations since Aug 2025, so for
Sep-Jan there are none: there we fall back to pending hunter claims (status
"reported", group Kreutz), trimmed to their 10-90th percentiles because about a
third of claims turn out not to be comets. Such envelopes are marked provisional.

Cost: comets of other groups (Kracht, Meyer, non-group: 10 of 116 confirmed
CCOR-1 reports) come from elsewhere and are rejected as "off-envelope".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from .velocity import FRAME_SECONDS, VelocityGrid

MIN_SAMPLES = 10
ANGLE_MARGIN = 15.0  # deg on each side
V_MARGIN = 2.0  # px/frame on each side of the velocity box
V_STEP = 0.5
TRIM = (10, 90)  # percentiles kept from provisional (claims-only) samples
MISS_MARGIN = 80.0  # px on each side of the miss-distance range
NOMINAL_SUN = (1024.0, 960.0)  # report px, for reports (no frame at hand); true centre within ~35 px


def _wrap(a):
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


def angle_of(dx: float, dy: float) -> float:
    """Angle (deg) of a report-coordinate vector, 0 = +x, 90 = up (-y)."""
    return float(np.degrees(np.arctan2(-dy, dx)))


@dataclass
class ArcRange:
    centre: float
    lo: float  # offsets from centre, deg
    hi: float

    @classmethod
    def of(cls, angles, trim=None, margin=ANGLE_MARGIN):
        a = np.radians(np.asarray(angles, float))
        c = float(np.degrees(np.arctan2(np.sin(a).mean(), np.cos(a).mean())))
        d = _wrap(np.asarray(angles) - c)
        lo, hi = (np.percentile(d, trim) if trim else (d.min(), d.max()))
        return cls(c, float(lo) - margin, float(hi) + margin)

    def __contains__(self, angle) -> bool:
        return self.lo <= float(_wrap(angle - self.centre)) <= self.hi

    def __str__(self):
        return f"{_wrap(self.centre + self.lo):.0f}..{_wrap(self.centre + self.hi):.0f}"


def miss_distance(x, y, vx, vy, sun) -> float:
    """Signed perpendicular distance (px) from the Sun to the line of motion.

    Positive when the Sun lies to the left of the direction of motion as seen
    on the image (report y points down, so 'left' uses the flipped-y frame)."""
    ux, uy = vx / np.hypot(vx, vy), -vy / np.hypot(vx, vy)  # unit motion, y up
    sx, sy = sun[0] - x, -(sun[1] - y)  # object -> Sun, y up
    return float(ux * sy - uy * sx)


@dataclass
class Envelope:
    month: int
    provisional: bool
    months_used: list
    samples: np.ndarray  # (n, 5): vx, vy, PA of first position, direction, miss distance
    direction: ArcRange
    miss: tuple[float, float]  # px, margin included
    grid: VelocityGrid

    def check(self, x, y, vx, vy, sun_xy) -> str | None:
        """None if a track at (x, y) moving (vx, vy) fits, else why not."""
        if angle_of(vx, vy) not in self.direction:
            return "off-envelope"
        if not self.miss[0] <= miss_distance(x, y, vx, vy, sun_xy or NOMINAL_SUN) <= self.miss[1]:
            return "off-envelope"
        return None

    def describe(self) -> str:
        src = "PROVISIONAL from hunter claims" if self.provisional else "confirmed"
        pa = ArcRange.of(self.samples[:, 2], margin=0)
        return (f"month {self.month:02d}: {len(self.samples)} {src} Kreutz tracks (months {self.months_used}); "
                f"entry PA {pa}; allowed direction {self.direction} deg, miss {self.miss[0]:.0f}..{self.miss[1]:.0f} px; "
                f"grid {self.grid.describe_box()}")


def kreutz_tracks(con, camera: str, status: str) -> list[tuple]:
    """(month, vx, vy, PA, direction, miss) per report: straight-line fit to the hunter's positions."""
    out = []
    rows = con.execute("SELECT report_id FROM reports WHERE camera=? AND status=? AND comet_group='Kreutz'",
                       (camera, status)).fetchall()
    for (rid,) in rows:
        f = con.execute("SELECT frame_time, x_ul, y_ul FROM frames WHERE report_id=? AND x_ul IS NOT NULL "
                        "AND frame_time IS NOT NULL ORDER BY seq", (rid,)).fetchall()
        if len(f) < 3:
            continue
        t = np.array([datetime.fromisoformat(r[0]).timestamp() for r in f]) / FRAME_SECONDS
        if np.ptp(t) == 0:
            continue
        x, y = np.array([r[1] for r in f]), np.array([r[2] for r in f])
        px, py = np.polyfit(t, x, 1), np.polyfit(t, y, 1)
        if np.hypot(x - np.polyval(px, t), y - np.polyval(py, t)).max() > 5 or np.hypot(px[0], py[0]) > 30:
            continue  # typo'd times/positions; same filter as velocity.report_velocities
        vx, vy = float(px[0]), float(py[0])
        pa = angle_of(x[0] - NOMINAL_SUN[0], y[0] - NOMINAL_SUN[1])
        out.append((int(f[0][0][5:7]), vx, vy, pa, angle_of(vx, vy), miss_distance(x[0], y[0], vx, vy, NOMINAL_SUN)))
    return out


def for_month(con, camera: str, month: int) -> Envelope:
    """Confirmed tracks within +-1 month if there are enough; else this month's
    claims (widening to +-2 months). The geometry turns ~20-30 deg a month, so
    same-month claims beat confirmations from further away."""
    tracks = {st: kreutz_tracks(con, camera, st) for st in ("confirmed", "reported")}
    for status, spread in (("confirmed", 0), ("confirmed", 1), ("reported", 0), ("reported", 1), ("reported", 2)):
        months = sorted({(month - 1 + k) % 12 + 1 for k in range(-spread, spread + 1)})
        s = np.array([t[1:] for t in tracks[status] if t[0] in months])
        if len(s) >= MIN_SAMPLES:
            return _build(month, status == "reported", months, s)
    raise SystemExit(f"no Kreutz {camera} tracks near month {month}; run `kreutz reports sync`")


def _build(month, provisional, months, s) -> Envelope:
    trim = TRIM if provisional else None
    v = s[:, :2]
    lo, hi = (np.percentile(v, trim[0], axis=0), np.percentile(v, trim[1], axis=0)) if trim else (v.min(0), v.max(0))
    lo = np.floor((lo - V_MARGIN) / V_STEP) * V_STEP
    hi = np.ceil((hi + V_MARGIN) / V_STEP) * V_STEP
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + V_STEP / 2, V_STEP), np.arange(lo[1], hi[1] + V_STEP / 2, V_STEP))
    grid = VelocityGrid(gx.ravel(), gy.ravel(), v, V_MARGIN, V_STEP)
    b = s[:, 4]
    blo, bhi = np.percentile(b, trim) if trim else (b.min(), b.max())
    return Envelope(month, provisional, months, s, ArcRange.of(s[:, 3], trim),
                    (float(blo) - MISS_MARGIN, float(bhi) + MISS_MARGIN), grid)
