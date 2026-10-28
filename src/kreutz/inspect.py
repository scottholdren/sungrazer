"""`kreutz inspect`: human-readable summary of one FITS frame plus a PNG preview."""

from __future__ import annotations

import struct
import warnings
import zlib
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS

from . import coords
from .fitsio import frame_quality, image_hdu_index, mid_time, obs_time

WCS_KEYS = ("WCSNAME", "CTYPE1", "CTYPE2", "CRPIX1", "CRPIX2", "CRVAL1", "CRVAL2",
            "CDELT1", "CDELT2", "CUNIT1", "CROTA", "RSUN_ARC", "DSUN_OBS")
INFO_KEYS = ("INSTRUME", "LEVEL", "BUNIT", "EXPTIME", "CDEXPMS", "NSUMEXP", "IMGTYPE",
             "RECTIFY", "L2RFLIP", "NROT90", "YAWFLIP")


def inspect(path: str, png: str | None = None) -> Path:
    path = Path(path)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", fits.verify.VerifyWarning)
        with fits.open(path) as hdul:
            print(f"File: {path}")
            for i, h in enumerate(hdul):
                shape = h.data.shape if h.data is not None else ()
                dtype = h.data.dtype if h.data is not None else "-"
                print(f"  HDU {i}: {type(h).__name__:<16} name={h.name:<18} shape={shape} dtype={dtype}")
            idx = image_hdu_index(hdul)
            hdr = hdul[idx].header.copy()
            data = np.asarray(hdul[idx].data, dtype=np.float32)
        primary = hdul[0].header

    print(f"\nImage HDU {idx}: {data.shape[1]} wide x {data.shape[0]} tall, {data.dtype}")
    print(f"Obs time (start): {obs_time(hdr, path).isoformat()}   mid-exposure: {mid_time(hdr, path).isoformat()}")
    print(f"Pipeline status: {primary.get('STATUS', '?')}")
    _print_keys("Instrument", hdr, INFO_KEYS)
    _print_keys("Pointing / WCS", hdr, WCS_KEYS)
    if "CDELT1" in hdr and "CRPIX1" in hdr:
        scale = abs(hdr["CDELT1"]) * 3600
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            col, row = (float(v) for v in WCS(hdr).world_to_pixel_values(0, 0))
        x, y = coords.fits_to_report(col, row, data.shape[0])
        rsun_px = hdr.get("RSUN_ARC", 0) / scale
        print(f"  -> plate scale {scale:.2f} arcsec/px; solar radius {rsun_px:.1f} px")
        print(f"  -> Sun centre (WCS) at report coords x={x:.1f}, y={y:.1f} "
              f"(origin upper-left, see README)")

    try:
        key = coords.check_orientation(hdr, source=path.name)
        print(f"Orientation flags {key}: verified")
    except coords.OrientationChanged as e:
        print(f"WARNING: {e}")

    q = frame_quality(hdr, data)
    print("\nQuality flags:", ", ".join(f"{k}={v}" for k, v in q.flags.items()))
    print("Data stats:", ", ".join(f"{k}={v:.3g}" for k, v in q.stats.items()))
    print("Verdict:", "OK" if q.ok else "PROBLEM -> " + "; ".join(q.problems))
    if q.warnings:
        print("Warnings (frame still usable):", "; ".join(q.warnings))

    out = Path(png) if png else path.with_suffix(".png")
    centre = (hdr["CRPIX2"] - 1, hdr["CRPIX1"] - 1) if "CRPIX1" in hdr else None
    write_png(out, stretch(data, centre))
    print(f"\nPreview: {out}  (displayed with upper-left origin, same as Sungrazer reports)")
    return out


def _print_keys(title, hdr, keys):
    present = [(k, hdr[k]) for k in keys if k in hdr]
    if present:
        print(f"{title}:")
        for k, v in present:
            print(f"  {k:<8} = {v}")


def stretch(data: np.ndarray, centre: tuple[float, float] | None = None) -> np.ndarray:
    """Map a frame to 8-bit for viewing, oriented for display (row 0 = top).

    The corona falls off steeply with distance from the Sun, so a plain linear
    scale shows only a bright blob. We divide out the typical brightness at each
    radius (a radial median), which flattens the corona and lets stars, comets
    and streamers show up, then clip and gamma-stretch.
    """
    d = np.where(np.isfinite(data), data, 0).astype(np.float64)
    h, w = d.shape
    yy, xx = np.indices(d.shape)
    # centre = (row, col) of the WCS reference pixel, which sits within a few
    # tens of pixels of the Sun; the frame centre is a fine fallback for a preview.
    cy, cx = centre if centre else (h / 2, w / 2)
    r = np.hypot(yy - cy, xx - cx).astype(int)
    pos = d > 0
    prof = np.zeros(r.max() + 1)
    for rad in range(0, r.max() + 1, 4):  # 4-px annuli
        ring = (r >= rad) & (r < rad + 4) & pos
        if ring.any():
            prof[rad:rad + 4] = np.median(d[ring])
    norm = np.where(prof[r] > 0, d / np.maximum(prof[r], 1e-30), 0)
    # After dividing, typical sky is ~1.0 but streamers and arm glare span 0.2x to
    # 100x, so show it on a log scale between fixed limits (1/4x .. 4x).
    lg = np.log10(np.maximum(norm, 1e-3))
    img = np.clip((lg + 0.6) / 1.2, 0, 1)
    return np.flipud((img * 255).astype(np.uint8))  # FITS row 1 is the bottom


def read_png(path: Path) -> np.ndarray:
    """Read an 8-bit grayscale PNG written by write_png (filter 0 on every row)."""
    data, pos, w, h, idat = path.read_bytes(), 8, 0, 0, b""
    while pos < len(data):
        n, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + n]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        pos += 12 + n
    raw = np.frombuffer(zlib.decompress(idat), np.uint8).reshape(h, w + 1)
    if (raw[:, 0] != 0).any():
        raise ValueError(f"{path}: not a filter-0 PNG")
    return raw[:, 1:].copy()


def write_png(path: Path, img: np.ndarray) -> None:
    """Write an 8-bit grayscale PNG using only the standard library."""
    h, w = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(tag, payload):
        return struct.pack(">I", len(payload)) + tag + payload + struct.pack(">I", zlib.crc32(tag + payload))

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 6))
                     + chunk(b"IEND", b""))
