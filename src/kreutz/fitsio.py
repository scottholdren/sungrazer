"""Reading CCOR FITS frames: image data, observation time, and frame quality.

File layout (verified on CCOR1 L1B, 2025 and 2026):
  HDU 0  empty primary header (pipeline version, STATUS)
  HDU 1  tile-compressed float32 image, 2048 wide x 1920 tall, plus ~400 keywords
We always use the first HDU that actually holds a 2-D image.

Times: DATE-OBS is exposure start and matches the timestamp in the filename,
which is what comet hunters quote in reports ("1215" = the 12:15:25 frame).
DATE-AVG is mid-exposure, better for precise motion fits. Both are UTC.
"""

from __future__ import annotations

import functools
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from astropy.io import fits

from . import coords

_FILENAME_TIME = re.compile(r"_(\d{8}T\d{6})_")


def filename_time(name: str) -> datetime:
    """Timestamp embedded in a CCOR filename, e.g. CCOR1_1B_20250717T120025_V00_NC.fits."""
    m = _FILENAME_TIME.search(Path(name).name)
    if not m:
        raise ValueError(f"no timestamp in filename {name!r}")
    return datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)


def parse_fits_time(value: str) -> datetime:
    """Parse a FITS ISO-8601 date string as UTC.

    Accepts '2025-07-17T12:00:25.701', with or without fractional seconds, an
    optional trailing 'Z', and a space instead of 'T'.
    """
    s = value.strip().rstrip("Z").replace(" ", "T")
    if "." in s:
        head, frac = s.split(".", 1)
        s = f"{head}.{(frac + '000000')[:6]}"  # fromisoformat wants 3 or 6 digits
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def obs_time(header, path: str | Path | None = None) -> datetime:
    """Frame time = exposure start (DATE-OBS, else DATE-BEG, else filename)."""
    for key in ("DATE-OBS", "DATE-BEG"):
        if header.get(key):
            return parse_fits_time(header[key])
    if path is not None:
        return filename_time(str(path))
    raise ValueError("no observation time in header")


def mid_time(header, path=None) -> datetime:
    if header.get("DATE-AVG"):
        return parse_fits_time(header["DATE-AVG"])
    return obs_time(header, path)


@dataclass
class Frame:
    path: Path
    data: np.ndarray  # float32, shape (1920, 2048); data[row, col], row 0 = FITS row 1
    header: fits.Header
    time: datetime  # exposure start, UTC
    camera: str


def image_hdu_index(hdul) -> int:
    for i, h in enumerate(hdul):
        if h.header.get("NAXIS", 0) == 2 and h.header.get("NAXIS1", 0) > 0:
            return i
    raise ValueError("no 2-D image HDU")


def read_frame(path: str | Path, check_orientation: bool = True) -> Frame:
    """Load a frame. Raises coords.OrientationChanged on unverified orientation flags."""
    path = Path(path)
    with warnings.catch_warnings():
        # L1B files carry a BLANK keyword on float data; harmless, astropy complains.
        warnings.simplefilter("ignore", fits.verify.VerifyWarning)
        with fits.open(path) as hdul:
            h = hdul[image_hdu_index(hdul)]
            data = np.asarray(h.data, dtype=np.float32)
            header = h.header.copy()
    camera = str(header.get("INSTRUME", "")).strip().lower() or path.name.split("_")[0].lower()
    if check_orientation:
        coords.check_orientation(header, camera, source=path.name)
    return Frame(path, data, header, obs_time(header, path), camera)


@functools.lru_cache(maxsize=6)
def read_frame_cached(path: str | Path) -> Frame:
    """read_frame with a small cache for display code that revisits recent frames. Don't mutate the result."""
    return read_frame(path)


# --- frame quality -------------------------------------------------------------

# Header flags that mean something is wrong with the frame. "_Q" flags in CCOR
# headers are True when the condition is GOOD (e.g. EARFOV_Q = "Earth is outside
# the field of view"), so False is the problem.
_GOOD_IF_TRUE = {
    "ISNORMAL": "pipeline flagged frame as abnormal",
    "DCMPRS_Q": "decompression problem",
    "IMGBLK_Q": "image blocks missing/bad",
    "EARFOV_Q": "Earth inside field of view",
    "MONFOV_Q": "Moon inside field of view",
    "BORSGT_Q": "boresight check failed",
    "GTTRAC_Q": "guide telescope not tracking",
}
# Flags that are reported but don't make a frame unusable (checked on real frames):
#   MONFOV_Q  the Moon covers a small disc; the detector masks it (2025-07-24).
#   BORSGT_Q  false every day ~03:45-06:30 UTC as the Sun passes close behind
#             the Earth seen from GEO; images look normal (July 2025).
ADVISORY = {"MONFOV_Q", "BORSGT_Q"}
MAX_OFFSUNDR = 100

_BAD_IF_NONZERO = {
    "E_ECLPSE": "Earth eclipse state set (Sun blocked by Earth)",
    "M_ECLPSE": "Moon eclipse state set",
    "BADBLK_N": "bad telemetry blocks",
    "MISBLK_N": "missing telemetry blocks",
}


@dataclass
class Quality:
    ok: bool
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)
    flags: dict[str, object] = field(default_factory=dict)


def frame_quality(header, data: np.ndarray | None = None) -> Quality:
    problems, warnings, flags = [], [], {}
    for key, why in _GOOD_IF_TRUE.items():
        if key in header:
            flags[key] = header[key]
            if header[key] is False:
                (warnings if key in ADVISORY else problems).append(f"{key}=F: {why}")
    for key, why in _BAD_IF_NONZERO.items():
        if key in header:
            flags[key] = header[key]
            if header[key] not in (0, False, None):
                problems.append(f"{key}={header[key]}: {why}")
    for key in ("DOORPOS", "MAJSTORM", "MINSTORM", "COSMICS", "SE_ANGLE"):
        if key in header:
            flags[key] = header[key]
    if str(header.get("DOORPOS", "OPEN")).strip() != "OPEN":
        problems.append(f"DOORPOS={header['DOORPOS'].strip()}: door not open")
    if header.get("MAJSTORM") is True:
        problems.append("MAJSTORM=T: major particle storm (many cosmic-ray hits)")
    # Off-Sun pointing (arcsec). Normal frames are < 25; the daily calibration
    # off-points (~07:00-07:45 UTC, Oct 2026) reach thousands. Not every off-pointed
    # frame shows it here, so stage A also checks CRVAL jumps (detect/frames.py).
    if (header.get("OFFSUNDR") or 0) > MAX_OFFSUNDR:
        problems.append(f"OFFSUNDR={header['OFFSUNDR']}: spacecraft off-pointed from the Sun")

    stats = {}
    if data is not None:
        finite = np.isfinite(data)
        stats["finite_frac"] = float(finite.mean())
        stats["zero_frac"] = float((data == 0).mean())
        vals = data[finite]
        if vals.size:
            p = np.percentile(vals, [1, 50, 99])
            stats.update(p01=float(p[0]), median=float(p[1]), p99=float(p[2]))
        # The occulter + arm leave ~10-15% of pixels near zero; far more than that
        # means a blank or truncated frame.
        if stats["finite_frac"] < 0.5 or stats["zero_frac"] > 0.5:
            problems.append("mostly empty image data")
    return Quality(ok=not problems, problems=problems, warnings=warnings, stats=stats, flags=flags)
