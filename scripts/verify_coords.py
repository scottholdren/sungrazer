"""Re-check the report coordinate convention against confirmed comets.

For every frame position in confirmed CCOR-1 reports that we have frames for,
cut a 51x51 box around where each hypothesis says the comet is, subtract a
background made from the median of frames 1-3 hours either side (the comet and
stars have moved away by then, the corona hasn't), and median-stack the boxes.
The right convention shows a peak at the centre; wrong ones show nothing.

    python scripts/verify_coords.py [--start 2025-07-17 --end 2025-07-25]

Result on 2025-07-17..24 (50 positions): "flip" (our convention) peaks at offset
(0, 0) at ~14x the background scatter; the three alternatives are flat.
"""

import argparse
import warnings
from datetime import datetime, timedelta

import numpy as np

from kreutz import coords, fetch, fitsio
from kreutz.reports import db

warnings.simplefilter("ignore")
R = 25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2025-07-17")
    ap.add_argument("--end", default="2025-07-25")
    ap.add_argument("--status", nargs="+", default=["confirmed"],
                    help="report statuses to use (recent data has only 'reported')")
    ap.add_argument("--allow-unverified", action="store_true",
                    help="read frames whose orientation flags are not yet verified (to verify them)")
    a = ap.parse_args()

    frames = [p for p in fetch.local_frames("ccor1") if p.name.endswith("_NC.fits")]
    times = np.array([fitsio.filename_time(p.name).timestamp() for p in frames])
    cache, flags = {}, set()

    def load(i):
        if i not in cache:
            f = fitsio.read_frame(frames[i], check_orientation=not a.allow_unverified)
            flags.add(coords.orientation_key(f.header))
            cache[i] = f.data
        return cache[i]

    def nearest(ts):
        i = int(np.argmin(abs(times - ts)))
        return i if abs(times[i] - ts) < 400 else None

    H, W = coords.CCOR_HEIGHT, coords.CCOR_WIDTH
    hyps = {
        "flip (ours)": lambda x, y: coords.report_to_fits(x, y),
        "no flip": lambda x, y: (x, y),
        "flip + x mirror": lambda x, y: (W - 1 - x, H - 1 - y),
        "x mirror": lambda x, y: (W - 1 - x, y),
    }
    stacks = {k: [] for k in hyps}
    rows = db.connect().execute(
        """SELECT f.* FROM frames f JOIN reports r USING (report_id)
           WHERE r.status IN ({}) AND r.camera = 'ccor1' AND f.x_ul IS NOT NULL
             AND f.frame_time BETWEEN ? AND ?""".format(",".join("?" * len(a.status))),
        (*a.status, a.start, a.end)).fetchall()
    for f in rows:
        # Hunters quote HHMM; CCOR-1 frames start at HH:MM:25.
        t = (datetime.fromisoformat(f["frame_time"]) + timedelta(seconds=25)).timestamp()
        i = nearest(t)
        if i is None:
            continue
        ref = [nearest(t + dh * 3600) for dh in (-3, -2.5, -2, -1.5, -1, 1, 1.5, 2, 2.5, 3)]
        ref = [j for j in ref if j is not None]
        if len(ref) < 5:
            continue
        for k, h in hyps.items():
            c, r = (int(round(v)) for v in h(f["x_ul"], f["y_ul"]))
            if not (R <= c < W - R and R <= r < H - R):
                continue
            box = np.s_[r - R:r + R + 1, c - R:c + R + 1]
            d = load(i)[box] - np.nanmedian([load(j)[box] for j in ref], axis=0)
            if np.nanstd(d) > 0:
                stacks[k].append(d / np.nanstd(d))
    print("orientation flags of frames used (camera, L2RFLIP, NROT90, YAWFLIP):", sorted(flags, key=str))
    for k, st in stacks.items():
        if not st:
            print(f"{k:<16} no usable positions (frames downloaded?)")
            continue
        m = np.nanmedian(st, axis=0)
        pk = np.unravel_index(np.nanargmax(m), m.shape)
        print(f"{k:<16} n={len(st):3d}  centre={m[R, R]:5.2f}  peak={np.nanmax(m):5.2f} "
              f"at offset (row {pk[0] - R:+d}, col {pk[1] - R:+d})  scatter={np.nanstd(m):.2f}")


if __name__ == "__main__":
    main()
