"""Velocity grid for shift-and-stack, derived from what real comets did.

For every confirmed comet report of the camera we fit a straight line to the
hunter's positions and take its velocity in report coordinates (px per
15-minute frame; +x right, +y down). The grid is the box covering all of them
plus a margin, sampled every 0.5 px/frame.

Reports whose positions are not on a line (residual > 5 px; typo'd times or
dates) and absurd speeds (> 30 px/frame) are left out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

FRAME_SECONDS = 900.0


@dataclass
class VelocityGrid:
    vx: np.ndarray  # (n,) report px/frame
    vy: np.ndarray
    samples: np.ndarray  # (m, 2) the measured comet velocities the box was built from
    margin: float
    step: float

    def __len__(self):
        return len(self.vx)

    def describe_box(self) -> str:
        return (f"vx {self.vx.min():.1f}..{self.vx.max():.1f}, vy {self.vy.min():.1f}..{self.vy.max():.1f} "
                f"= {len(self)} velocities")

    def key(self) -> str:
        """Short id of the grid, so cached stack peaks are tied to the grid that made them."""
        import hashlib

        return hashlib.sha1(np.round(np.c_[self.vx, self.vy], 3).tobytes()).hexdigest()[:8]

    def describe(self) -> str:
        s = self.samples
        return (f"{len(s)} confirmed reports: vx {s[:, 0].min():.1f}..{s[:, 0].max():.1f}, "
                f"vy {s[:, 1].min():.1f}..{s[:, 1].max():.1f} px/frame; grid "
                f"vx {self.vx.min():.1f}..{self.vx.max():.1f}, vy {self.vy.min():.1f}..{self.vy.max():.1f} "
                f"step {self.step} (+-{self.margin} margin) = {len(self)} velocities")


def report_velocities(con, camera: str) -> np.ndarray:
    out = []
    for (rid,) in con.execute("SELECT report_id FROM reports WHERE camera=? AND status='confirmed'", (camera,)):
        f = con.execute("SELECT frame_time, x_ul, y_ul FROM frames WHERE report_id=? AND x_ul IS NOT NULL "
                        "AND frame_time IS NOT NULL ORDER BY seq", (rid,)).fetchall()
        if len(f) < 3:
            continue
        t = np.array([datetime.fromisoformat(r[0]).timestamp() for r in f]) / FRAME_SECONDS
        if np.ptp(t) == 0:
            continue
        x, y = np.array([r[1] for r in f]), np.array([r[2] for r in f])
        px, py = np.polyfit(t, x, 1), np.polyfit(t, y, 1)
        resid = np.hypot(x - np.polyval(px, t), y - np.polyval(py, t)).max()
        if resid > 5 or np.hypot(px[0], py[0]) > 30:
            continue
        out.append((px[0], py[0]))
    return np.array(out)


def from_answer_key(con, camera: str, margin: float = 2.0, step: float = 0.5) -> VelocityGrid:
    v = report_velocities(con, camera)
    if len(v) < 10:
        raise SystemExit(f"only {len(v)} usable confirmed {camera} reports; run `kreutz reports sync`")
    lo = np.floor((v.min(0) - margin) / step) * step
    hi = np.ceil((v.max(0) + margin) / step) * step
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + step / 2, step), np.arange(lo[1], hi[1] + step / 2, step))
    return VelocityGrid(gx.ravel(), gy.ravel(), v, margin, step)
