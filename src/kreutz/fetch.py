"""Download coronagraph frames from the NCEI archive and the SWPC real-time feed.

Both sources use identical filenames (e.g. CCOR1_1B_20250717T000025_V00_NC.fits),
so a frame is stored once at data/frames/<camera>/<YYYYMMDD>/<filename> no matter
which source supplied it. Existing files are never re-downloaded.
"""

from __future__ import annotations

import re
import warnings
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree

from astropy.io import fits

from . import cameras, coords, net, paths
from .fitsio import filename_time, image_hdu_index

S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}


def list_archive_day(cam: cameras.Camera, day: date) -> list[tuple[str, int]]:
    """Return [(key, size)] of FITS files for one day, trying levels in order."""
    for level in cam.levels:
        prefix = f"{cam.ncei_prefix}/{level}/{day:%Y/%m/%d}/"
        items = _s3_list(prefix)
        items = [(k, s) for k, s in items if k.endswith(".fits")]
        if items:
            return items
    return []


def _s3_list(prefix: str) -> list[tuple[str, int]]:
    out, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix}
        if token:
            params["continuation-token"] = token
        root = ElementTree.fromstring(net.get(cameras.NCEI_BASE, params=params).content)
        for c in root.findall("s3:Contents", S3_NS):
            out.append((c.findtext("s3:Key", namespaces=S3_NS), int(c.findtext("s3:Size", namespaces=S3_NS))))
        if root.findtext("s3:IsTruncated", namespaces=S3_NS) != "true":
            return out
        token = root.findtext("s3:NextContinuationToken", namespaces=S3_NS)


def fetch_archive(camera_id: str, start: date, end: date) -> dict[str, int]:
    """Download every frame from start to end inclusive. Returns counts."""
    cam = cameras.get(camera_id)
    counts = {"downloaded": 0, "already_had": 0, "days_missing": 0}
    day = start
    while day <= end:
        items = list_archive_day(cam, day)
        if not items:
            counts["days_missing"] += 1
            print(f"{day}: no frames in archive")
        new = 0
        for key, size in items:
            name = key.rsplit("/", 1)[1]
            dest = paths.frame_dir(cam.id, f"{day:%Y%m%d}") / name
            if net.download(f"{cameras.NCEI_BASE}/{key}", dest, expected_size=size):
                new += 1
        counts["downloaded"] += new
        counts["already_had"] += len(items) - new
        if items:
            print(f"{day}: {len(items)} frames ({new} new); {orientation_note(cam.id, day)}", flush=True)
        day += timedelta(days=1)
    return counts


def fetch_live(camera_id: str, hours: float = 6, force: bool = False) -> dict[str, int]:
    """Download frames from the last `hours` of the SWPC real-time index that we lack.

    The index covers ~30 days (~20 GB), so we deliberately don't take all of it;
    use the archive fetch for history.
    """
    cam = cameras.get(camera_id)
    allowed, wait = net.poll_allowed(f"live:{cam.id}", force=force)
    if not allowed:
        print(f"last poll of {cam.id} live feed was <15 min ago; next allowed in {wait / 60:.1f} min")
        return {"downloaded": 0, "already_had": 0}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    counts = {"downloaded": 0, "already_had": 0}
    days: set[date] = set()
    for url in live_urls(cam):
        name = url.rsplit("/", 1)[1]
        t = filename_time(name)
        if t < cutoff:
            continue
        dest = paths.frame_dir(cam.id, f"{t:%Y%m%d}") / name
        days.add(t.date())
        if net.download(url, dest):
            counts["downloaded"] += 1
        else:
            counts["already_had"] += 1
    for d in sorted(days):
        print(f"{d}: {orientation_note(cam.id, d)}")
    return counts


def orientation_note(camera_id: str, day: date) -> str:
    """Orientation flags of the day's first frame on disk, and whether they're verified."""
    frames = local_frames(camera_id, day, day)
    if not frames:
        return "no frames"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(frames[0]) as hdul:
            h = hdul[image_hdu_index(hdul)].header
    key = coords.orientation_key(h, camera_id)
    ok = "verified" if key in coords.VERIFIED_ORIENTATIONS else "NOT VERIFIED"
    return f"orientation (L2RFLIP, NROT90, YAWFLIP) = {key[1:]} {ok}"


def live_urls(cam: cameras.Camera) -> list[str]:
    """Absolute URLs of FITS frames currently on the SWPC real-time feed.

    fits.json is the documented index, but SWPC rewrites it every few minutes and
    we have caught it served as "[]" mid-rewrite; the Apache directory listing
    next to it is the fallback.
    """
    urls = [e["url"] for e in net.get(cam.live_index).json() if e.get("url", "").endswith(".fits")]
    if not urls:
        listing_url = cam.live_index.rsplit("/", 1)[0] + "/fits/"
        names = re.findall(r'href="([^"/]+\.fits)"', net.get(listing_url).text)
        urls = [listing_url + n for n in dict.fromkeys(names)]
    return [cameras.SWPC_BASE + u if u.startswith("/") else u for u in urls]


def local_frames(camera_id: str, start: date | None = None, end: date | None = None):
    """Sorted list of FITS paths on disk for a camera, optionally within dates."""
    root = paths.FRAMES / camera_id
    if not root.exists():
        return []
    out = []
    for d in sorted(root.iterdir()):
        if not re.fullmatch(r"\d{8}", d.name):
            continue
        dd = date(int(d.name[:4]), int(d.name[4:6]), int(d.name[6:]))
        if (start and dd < start) or (end and dd > end):
            continue
        out.extend(sorted(d.glob("*.fits")))
    return out
