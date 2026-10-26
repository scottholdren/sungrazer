"""Download coronagraph frames from the NCEI archive and the SWPC real-time feed.

Both sources use identical filenames (e.g. CCOR1_1B_20250717T000025_V00_NC.fits),
so a frame is stored once at data/frames/<camera>/<YYYYMMDD>/<filename> no matter
which source supplied it. Existing files are never re-downloaded.
"""

from __future__ import annotations

import json
import re
import warnings
from datetime import date, datetime, timedelta, timezone
from xml.etree import ElementTree

import numpy as np
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
    use the archive fetch for history. Every poll also logs when each file was
    published (record_publication), for the latency in the scorecard's leads.
    """
    cam = cameras.get(camera_id)
    allowed, wait = net.poll_allowed(f"live:{cam.id}", force=force)
    if not allowed:
        print(f"last poll of {cam.id} live feed was <15 min ago; next allowed in {wait / 60:.1f} min")
        return {"downloaded": 0, "already_had": 0}
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)
    counts = {"downloaded": 0, "already_had": 0}
    days: set[date] = set()
    listing = live_listing(cam)
    record_publication(cam.id, listing, now)
    for url in [u for _, u, _ in listing] or live_urls(cam):
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
    lag = publication_lag(cam.id)
    if lag:
        print(lag["text"])
    return counts


_LISTED = re.compile(r'href="([^"/]+\.fits)">[^<]*</a>\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})')


def live_listing(cam: cameras.Camera, html: str | None = None) -> list[tuple[str, str, datetime]]:
    """(name, url, published) for every FITS file in the SWPC directory listing.

    The listing's modification time (UTC, to the minute) is when the file
    appeared: checked 2026-10-25 22:49 UTC, the newest file (22:30 frame) was
    stamped 22:46."""
    listing_url = cam.live_index.rsplit("/", 1)[0] + "/fits/"
    html = net.get(listing_url).text if html is None else html
    out = {}
    for name, stamp in _LISTED.findall(html):
        out[name] = (name, listing_url + name, datetime.strptime(stamp, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc))
    return list(out.values())


def _pub_log(camera_id: str):
    return paths.STATE_DIR / f"published_{camera_id}.json"


def record_publication(camera_id: str, listing, seen_at: datetime) -> None:
    """Log obs time, listing time and our first-seen time for each live file.

    first_seen only measures latency if we were already watching (previous poll
    <= 20 min earlier); otherwise it's the backlog of a first poll."""
    p = _pub_log(camera_id)
    log = json.loads(p.read_text()) if p.exists() else {"polls": [], "files": {}}
    watching = bool(log["polls"]) and (seen_at - datetime.fromisoformat(log["polls"][-1])) <= timedelta(minutes=20)
    for name, _, published in listing:
        if name not in log["files"]:
            log["files"][name] = {"obs": filename_time(name).isoformat(), "listed": published.isoformat(),
                                  "first_seen": seen_at.isoformat(timespec="seconds"), "watching": watching}
    log["polls"].append(seen_at.isoformat(timespec="seconds"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(log, indent=0))


def publication_lags(camera_id: str) -> list[float] | None:
    """Minutes from each file's exposure start to its appearance in the SWPC listing."""
    p = _pub_log(camera_id)
    if not p.exists():
        return None
    files = json.loads(p.read_text())["files"].values()
    return [(datetime.fromisoformat(f["listed"]) - datetime.fromisoformat(f["obs"])).total_seconds() / 60
            for f in files]


def publication_lag(camera_id: str) -> dict | None:
    """Minutes from exposure start to publication on SWPC (listing time), and to
    our first sight of the file while watching."""
    p = _pub_log(camera_id)
    if not p.exists():
        return None
    files = json.loads(p.read_text())["files"].values()
    obs = lambda f: datetime.fromisoformat(f["obs"])
    listed = np.array([(datetime.fromisoformat(f["listed"]) - obs(f)).total_seconds() / 60 for f in files])
    seen = np.array([(datetime.fromisoformat(f["first_seen"]) - obs(f)).total_seconds() / 60
                     for f in files if f["watching"]])
    if not len(listed):
        return None
    out = {"n": len(listed), "median_min": float(np.median(listed)), "p90_min": float(np.percentile(listed, 90)),
           "n_watched": len(seen), "seen_median_min": float(np.median(seen)) if len(seen) else None}
    first, last = min(obs(f) for f in files), max(obs(f) for f in files)
    out["text"] = (f"SWPC publication lag ({camera_id}, {out['n']} files {first:%m-%d}..{last:%m-%d}): "
                   f"median {out['median_min']:.1f} min, 90th percentile {out['p90_min']:.1f} min after exposure start"
                   + (f"; first seen by our 15-min polls: median {out['seen_median_min']:.1f} min ({len(seen)} files)"
                      if len(seen) else "; no files first seen while watching yet"))
    return out


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
