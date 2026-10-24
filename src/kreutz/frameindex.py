"""Find the frame on disk that a report (or a detection) refers to.

Hunters quote frame times as HHMM; CCOR-1 frames start at HH:MM:25 (CCOR-2 at
HH:MM:14). We take the nearest frame of the same camera within 2 minutes and
treat anything further away as "no such frame" rather than guessing.
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, timedelta
from pathlib import Path

from . import fetch
from .fitsio import filename_time

MATCH_TOLERANCE = timedelta(minutes=2)


class FrameIndex:
    def __init__(self, camera: str, start: date | None = None, end: date | None = None):
        self.camera = camera
        self.paths: list[Path] = fetch.local_frames(camera, start, end)
        self.times: list[datetime] = [filename_time(p.name) for p in self.paths]

    def __len__(self):
        return len(self.paths)

    def nearest(self, t: datetime, tolerance: timedelta = MATCH_TOLERANCE) -> int | None:
        """Index of the frame closest to t, or None if none is within tolerance."""
        i = bisect.bisect_left(self.times, t)
        best = None
        for j in (i - 1, i):
            if 0 <= j < len(self.times):
                if best is None or abs(self.times[j] - t) < abs(self.times[best] - t):
                    best = j
        if best is None or abs(self.times[best] - t) > tolerance:
            return None
        return best


def match_report(con, report_id: str, index: FrameIndex):
    """[(frame row, frame index or None)] for a report; camera must match the index."""
    cam = con.execute("SELECT camera FROM reports WHERE report_id=?", (report_id,)).fetchone()
    if cam is None or cam["camera"] != index.camera:
        raise ValueError(f"{report_id} is not a {index.camera} report")
    rows = con.execute("SELECT * FROM frames WHERE report_id=? AND frame_time IS NOT NULL ORDER BY seq",
                       (report_id,)).fetchall()
    return [(r, index.nearest(datetime.fromisoformat(r["frame_time"]))) for r in rows]
