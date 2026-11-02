"""Positions in solar coordinates, so the two CCOR cameras can be compared.

CCOR-1 (GOES-19, geostationary) and CCOR-2 (SWFO-L1, at L1) look at the same
corona from ~1.5 million km apart, with different plate scales and rolls. A
hunter's report on one camera is turned into the other camera's pixels through
each frame's WCS (helioprojective longitude/latitude, ZPN projection): report
pixel -> (Tx, Ty) in solar radii as seen by that observer -> the other camera's
pixel at the same time. Working in solar radii (RSUN_ARC, which scales with the
observer's distance) absorbs most of the parallax for an object near the Sun:
the same comet measured 0.2 Rsun apart in arcsec terms on 2026-11-02 and
agreed to better than that in Rsun.

Report pixels follow the hunters' convention: origin upper-left, y down, on the
image as displayed, which for both cameras is the FITS array flipped vertically
(verified for CCOR-1 by scripts/verify_coords.py, and for CCOR-2 by the
2026-11-02 comet landing on the same solar position from both cameras).

The WCS comes from the frame nearest the time on disk; for a camera we do not
archive, the nearest frame in SWPC's live listing is fetched once and kept.
"""

from __future__ import annotations

import functools
import math
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import cameras, coords, fetch, net, paths
from .fitsio import filename_time

NEAR_H = 12.0  # a frame this close in time is good enough for the WCS (the roll drifts ~0.1 deg/day)
_LISTING_TTL = timedelta(minutes=15)
_listings: dict[str, tuple[datetime, list]] = {}


class NoFrame(Exception):
    pass


@functools.lru_cache(maxsize=64)
def _wcs(path: str):
    from astropy.io import fits
    from astropy.wcs import WCS

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        with fits.open(path) as hdul:
            h = next(x.header for x in hdul if x.header.get("NAXIS", 0) == 2)
            w = WCS(h, naxis=2)
            return w, float(h["RSUN_ARC"]), int(h["NAXIS2"]), int(h["NAXIS1"])


def frame_for(camera: str, t: datetime, fetch_missing: bool = True, any_age: bool = False) -> Path:
    """The frame on disk nearest t (within NEAR_H hours); if none and allowed, the
    nearest in the camera's live listing is downloaded; with `any_age`, failing
    both, the nearest frame on disk however old (the roll drifts a few degrees
    over months: fine for the velocity envelope, not for matching). Raises NoFrame."""
    t = t.astimezone(timezone.utc)
    best = _nearest(fetch.local_frames(camera, (t - timedelta(hours=NEAR_H)).date(), (t + timedelta(hours=NEAR_H)).date()), t)
    if best is not None:
        return best
    if any_age and not fetch_missing:
        return _any_frame(camera, t)
    if fetch_missing:
        cam = cameras.get(camera)
        now = datetime.now(timezone.utc)
        cached = _listings.get(camera)
        if cached is None or now - cached[0] > _LISTING_TTL:
            try:
                _listings[camera] = cached = (now, fetch.live_listing(cam))
            except Exception as e:  # noqa: BLE001 - offline: fall through to NoFrame
                raise NoFrame(f"{camera}: no frame near {t:%Y-%m-%d %H:%M} on disk and the live listing failed: {e}")
        near = [(name, url, filename_time(name)) for name, url, _ in cached[1]]
        near = [n for n in near if abs(n[2] - t) <= timedelta(hours=NEAR_H)]
        if near:
            name, url, ft = min(near, key=lambda n: abs(n[2] - t))
            dest = paths.frame_dir(camera, ft.strftime("%Y%m%d")) / name
            net.download(url, dest)
            if dest.exists():
                return dest
    if any_age:
        return _any_frame(camera, t)
    raise NoFrame(f"{camera}: no frame within {NEAR_H:g} h of {t:%Y-%m-%d %H:%M}")


@functools.lru_cache(maxsize=8)
def _all_frames(camera: str) -> tuple:
    return tuple(fetch.local_frames(camera))


def _any_frame(camera: str, t: datetime) -> Path:
    frames = _all_frames(camera)
    if not frames:
        raise NoFrame(f"{camera}: no frames on disk at all")
    return min(frames, key=lambda p: abs(filename_time(p.name) - t))


def _nearest(frames: list[Path], t: datetime) -> Path | None:
    best, dt = None, timedelta(hours=NEAR_H)
    for p in frames:
        d = abs(filename_time(p.name) - t)
        if d <= dt:
            best, dt = p, d
    return best


def to_solar(camera: str, t: datetime, x: float, y: float, fetch_missing: bool = True, any_age: bool = False) -> tuple[float, float]:
    """Report pixel (upper-left origin, y down) -> (Tx, Ty) in solar radii as seen from that camera."""
    w, rsun, h, _ = _wcs(str(frame_for(camera, t, fetch_missing, any_age)))
    col, row = coords.report_to_fits(x, y, h)
    tx, ty = w.all_pix2world([[col, row]], 0)[0]
    tx, ty = (tx + 180) % 360 - 180, (ty + 180) % 360 - 180
    return tx * 3600 / rsun, ty * 3600 / rsun


def from_solar(camera: str, t: datetime, rx: float, ry: float, fetch_missing: bool = True, any_age: bool = False) -> tuple[float, float]:
    """(Tx, Ty) in solar radii -> report pixel in that camera (may fall outside the frame)."""
    w, rsun, h, _ = _wcs(str(frame_for(camera, t, fetch_missing, any_age)))
    col, row = w.all_world2pix([[rx * rsun / 3600, ry * rsun / 3600]], 0)[0]
    return coords.fits_to_report(float(col), float(row), h)


def inside(camera: str, x: float, y: float, margin: float = 0.0) -> bool:
    return margin <= x < coords.CCOR_WIDTH - margin and margin <= y < coords.CCOR_HEIGHT - margin


def cross_positions(src: str, dst: str, times, xs, ys, fetch_missing: bool = True, any_age: bool = False):
    """Positions reported on camera `src` expressed in camera `dst`'s report pixels at
    the same times. Returns (times, xs, ys) keeping only the positions that could
    be converted and land inside dst's frame; empty arrays if the WCS of either
    camera is unavailable for those times."""
    out_t, out_x, out_y = [], [], []
    for ts, x, y in zip(times, xs, ys):
        t = datetime.fromtimestamp(float(ts), timezone.utc)
        try:
            rx, ry = to_solar(src, t, float(x), float(y), fetch_missing, any_age)
            px, py = from_solar(dst, t, rx, ry, fetch_missing, any_age)
        except (NoFrame, KeyError, ValueError):
            continue
        if math.isfinite(px) and math.isfinite(py) and inside(dst, px, py):
            out_t.append(float(ts))
            out_x.append(px)
            out_y.append(py)
    return np.array(out_t), np.array(out_x), np.array(out_y)


def other_ccor(camera: str) -> str | None:
    return {"ccor1": "ccor2", "ccor2": "ccor1"}.get(camera)
