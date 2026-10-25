"""Solve how a report's quoted pixels map onto CCOR pixels: scale plus offset.

Sungrazer's form lets hunters declare an image size. Most quote 2048x1920
(full-resolution FITS); some declare 1024x1024 or 1024x960 (the SWPC JPEGs,
which are the L3 images downsampled 2x). For a hypothesis full = s * quoted + o
we find the offset o by cross-correlation: for each quoted position, take the
background-subtracted frame at that time (median of frames 1-3 h either side
removes the corona, as in verify_coords.py), and add it up shifted so the
hypothesised position lands on a common point, for every offset at once. A
moving comet only adds up coherently when the scale is right (otherwise its
motion is stretched), and then only at the right offset.

    python scripts/verify_report_scale.py --size 1024x1024 --status confirmed --download
    python scripts/verify_report_scale.py --reports report_bc_20261019233808 report_bc_20261023150123

--download fetches only the frames these reports need (their times +-3.5 h)
from the NCEI archive, into the usual data/frames/ cache.
"""

import argparse
import warnings
from datetime import datetime, timedelta

import numpy as np

from kreutz import cameras, coords, fetch, fitsio, net, paths
from kreutz.reports import db

warnings.simplefilter("ignore")
REF_HOURS = (-3, -2.5, -2, -1.5, -1, 1, 1.5, 2, 2.5, 3)
SPAN = 1000  # px: offsets searched in each axis, full resolution
CLIP = 8.0  # sigma: stars and cosmic rays can't dominate the sum
HYPOTHESES = {"scale 1": (1.0, 1.0), "scale 2": (2.0, 2.0), "scale 2 x 1.875": (2.0, 1920 / 1024)}


def positions(con, a):
    if a.reports:
        q = ",".join("?" * len(a.reports))
        rows = con.execute(f"SELECT report_id FROM reports WHERE report_id IN ({q})", a.reports).fetchall()
    else:
        rows = con.execute("SELECT report_id FROM reports WHERE camera='ccor1' AND image_size LIKE ? "
                           "AND status = ?", (a.size + "%", a.status)).fetchall()
    out = {}
    for (rid,) in rows:
        f = con.execute("SELECT frame_time, x_ul, y_ul FROM frames WHERE report_id=? AND x_ul IS NOT NULL "
                        "AND frame_time IS NOT NULL", (rid,)).fetchall()
        # Hunters quote HHMM; CCOR-1 frames start ~25 s later.
        pts = [((datetime.fromisoformat(t) + timedelta(seconds=25)).timestamp(), x, y) for t, x, y in f]
        if pts:
            out[rid] = pts
    return out


def download(pos):
    cam = cameras.get("ccor1")
    want = []
    for pts in pos.values():
        ts = [p[0] for p in pts]
        want.append((min(ts) - 3.5 * 3600, max(ts) + 3.5 * 3600))
    days = sorted({datetime.utcfromtimestamp(t).date() for lo, hi in want for t in (lo, hi)})
    for day in days:
        n = 0
        for key, size in fetch.list_archive_day(cam, day):
            name = key.rsplit("/", 1)[1]
            if not name.endswith("_NC.fits"):
                continue
            t = fitsio.filename_time(name).timestamp()
            if any(lo <= t <= hi for lo, hi in want):
                n += net.download(f"{cameras.NCEI_BASE}/{key}", paths.frame_dir("ccor1", f"{day:%Y%m%d}") / name,
                                  expected_size=size)
        print(f"{day}: {n} new frames", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", default="1024x1024")
    ap.add_argument("--status", default="confirmed")
    ap.add_argument("--reports", nargs="*")
    ap.add_argument("--download", action="store_true")
    a = ap.parse_args()
    pos = positions(db.connect(), a)
    if a.download:
        download(pos)

    frames = [p for p in fetch.local_frames("ccor1") if p.name.endswith("_NC.fits")]
    times = np.array([fitsio.filename_time(p.name).timestamp() for p in frames])
    cache, flags = {}, set()

    def load(i):
        if i not in cache:
            f = fitsio.read_frame(frames[i], check_orientation=False)
            flags.add(coords.orientation_key(f.header))
            cache[i] = f.data
        return cache[i]

    def nearest(ts):
        i = int(np.argmin(abs(times - ts)))
        return i if abs(times[i] - ts) < 400 else None

    H, W = coords.CCOR_HEIGHT, coords.CCOR_WIDTH
    diffs = []  # (report, quoted x, y, background-subtracted frame in sigma, padded)
    for rid, pts in pos.items():
        for t, x, y in pts:
            i = nearest(t)
            ref = [j for j in (nearest(t + dh * 3600) for dh in REF_HOURS) if j is not None]
            if i is None or len(ref) < 5:
                continue
            d = load(i) - np.nanmedian([load(j) for j in ref], axis=0)
            s = 1.4826 * np.nanmedian(np.abs(d - np.nanmedian(d)))
            d = np.clip(np.nan_to_num(d / s), -CLIP, CLIP).astype(np.float32)
            diffs.append((rid, x, y, np.pad(d, SPAN), np.pad(np.ones(d.shape, np.uint8), SPAN)))
        cache.clear()  # frames are 16 MB each; reports are far apart in time
    print(f"{len(diffs)} positions from {len({d[0] for d in diffs})} of {len(pos)} reports "
          f"(others lack frames: use --download); orientation flags {sorted(flags, key=str)}")
    if not diffs:
        return
    # The direct test at zero offset (as in verify_coords.py): median of 51x51
    # boxes around each hypothesised position; the right mapping peaks at the centre.
    R = 25
    for name, (sx, sy) in HYPOTHESES.items():
        boxes = []
        for _, x, y, d, _ in diffs:
            col, row = coords.report_to_fits(sx * x, sy * y)
            c, r = int(round(col)) + SPAN, int(round(row)) + SPAN
            if SPAN + R <= r < SPAN + H - R and SPAN + R <= c < SPAN + W - R:
                boxes.append(d[r - R:r + R + 1, c - R:c + R + 1])
        if not boxes:
            print(f"{name:<16} box test: no positions inside the image")
            continue
        m = np.median(boxes, axis=0)
        pk = np.unravel_index(np.argmax(m), m.shape)
        print(f"{name:<16} box test n={len(boxes):3d}: centre {m[R, R]:5.2f}, peak {m.max():5.2f} at "
              f"(row {pk[0] - R:+d}, col {pk[1] - R:+d}), scatter {np.std(m):.2f}")
    for name, (sx, sy) in HYPOTHESES.items():
        acc = np.zeros((2 * SPAN + 1, 2 * SPAN + 1), np.float32)
        cover = np.zeros(acc.shape, np.int32)
        n = 0
        for _, x, y, d, inside in diffs:
            col, row = coords.report_to_fits(sx * x, sy * y)
            c, r = int(round(col)) + SPAN, int(round(row)) + SPAN  # in padded array
            if not (0 <= r - SPAN and r + SPAN < d.shape[0] and 0 <= c - SPAN and c + SPAN < d.shape[1]):
                continue
            # acc[oy, ox] += frame at (row + oy, col + ox): report y down = row up, so oy is -dy
            acc += d[r - SPAN:r + SPAN + 1, c - SPAN:c + SPAN + 1]
            cover += inside[r - SPAN:r + SPAN + 1, c - SPAN:c + SPAN + 1]
            n += 1
        if not n:
            print(f"{name:<16} no positions fit inside the image")
            continue
        acc /= np.sqrt(n)
        acc[cover < max(n * 0.8, 1)] = 0  # offsets that push most positions off the image
        sel = acc[cover >= n * 0.8]
        noise = 1.4826 * np.median(np.abs(sel - np.median(sel)))
        pk = np.unravel_index(np.argmax(acc), acc.shape)
        drow, dcol = pk[0] - SPAN, pk[1] - SPAN
        # offset in report coordinates: x = col, y = H-1-row -> dx = dcol, dy = -drow
        print(f"{name:<16} n={n:3d}  best offset report (dx, dy) = ({dcol:+d}, {-drow:+d})  "
              f"peak {acc[pk] / noise:5.1f}x scatter;  at (0,0): {acc[SPAN, SPAN] / noise:5.1f}x")
        top = np.argsort(acc.ravel())[::-1][:200]
        seen = []
        for k in top:
            r, c = divmod(int(k), acc.shape[1])
            if all(abs(r - r2) > 10 or abs(c - c2) > 10 for r2, c2 in seen):
                seen.append((r, c))
            if len(seen) == 4:
                break
        print("                 next peaks: " + ", ".join(
            f"({c - SPAN:+d},{-(r - SPAN):+d}) {acc[r, c] / noise:.1f}x" for r, c in seen[1:]))


if __name__ == "__main__":
    main()
