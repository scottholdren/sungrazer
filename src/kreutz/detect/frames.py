"""Stage A: turn each frame into a significance map with background and stars removed.

For frame i (all steps NaN-aware):
  1. background  = median of the previous 16 good frames (~4 h). Anything static
                   that long (corona, occulter glow, dust) is background.
     diff        = frame - background
  2. stars       : find the ~300 brightest point sources in diff_i and in each of
                   diff_{i-1..i-8}; solve the star-field motion (translation +
                   small rotation) from each earlier frame to this one; warp the
                   earlier diffs onto this frame; their median is a stars-only
                   model (a moving comet is in a different place in each, so the
                   median drops it). residual = diff - star model; pixels within
                   4 px of stars in the model are masked (imperfect subtraction).
                   The 1-frame drift vector is recorded as an orientation check.
  3. DoG         : difference of Gaussians (1.2 px core, 4.0 px surround) keeps
                   point sources; divided by the local noise -> significance.
  4. downsample  : 2x2 sums / 2 (keeps unit noise) for the stacking stage.

Frames are left out of everything when a header flag says so (fitsio.frame_quality:
Earth in the field, eclipse, off-Sun pointing...) or when the pointing has jumped:
CRVAL moved more than 60" from the last accepted frame. The daily calibration
off-points (Oct 2026: 07:00-07:45 UTC) jump by ~1 deg while OFFSUNDR on some of
them still reads ~10, so the header flag alone isn't enough. If 4 frames in a row
agree on a new pointing it is accepted as the new normal.

Frames right next to one with the Earth in the field or an eclipse are dropped
too: they carry scattered Earthlight without being flagged (Oct 2026: the 04:00
and 05:30 frames around the daily 04:15-05:15 Earth transit fail star
registration).

A frame is "warm" (is_warm) when its star model is near full depth and its
background frames are recent. Right after a gap neither holds, and badly
subtracted stars and corona make fake movers; stage C starts no new tracks from
windows containing cold frames (existing tracks may continue through them).

Results are cached per frame in data/derived/<camera>/<VERSION>/ so reruns and
the stacking stage are cheap. `dump_stages` writes every intermediate array.
"""

from __future__ import annotations

import json
import warnings
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from .. import coords, paths, stars
from ..fitsio import Frame, frame_quality, read_frame

VERSION = "a4"
BG_FRAMES = 16
STAR_FRAMES = 8
MIN_STAR_FRAMES = 4
STAR_MASK_RADIUS = 4
STAR_MASK_SIGMA = 8.0
EDGE_GUARD = 6  # px next to NaN regions dropped from the significance map (DoG surround reaches in)
SIG_CLIP = 50.0
MOON_RATIO = 1.3  # pixel > 1.3x background ...
MOON_MIN_AREA = 2000  # ... over this many connected px = the Moon (disc ~7000 px); masked
MOON_MARGIN = 25
POINT_JUMP = 60.0  # arcsec of CRVAL movement between accepted frames = off-pointed
POINT_REBASE = 4  # this many consecutive frames agreeing on a new pointing -> accept it
EARTH_GUARD = 1  # frames on each side of an Earth-in-field / eclipse frame also dropped
WARM_STAR_FRAMES = 6  # of STAR_FRAMES
WARM_BG_SPAN = 24  # 15-min slots (6 h) the 16 background frames may span


def derived_dir(camera: str) -> Path:
    return paths.DATA / "derived" / camera / VERSION


def is_standard(path: Path) -> bool:
    """Regular full-resolution frames end in _NC.fits. Binned campaign frames
    (*_0C.fits, 1024x960, 5-min cadence, e.g. 2025-07-18 03:45-06:20) are skipped."""
    return path.name.endswith("_NC.fits")


@dataclass
class FrameMeta:
    name: str
    time: float  # unix seconds, exposure start
    ok: bool
    problems: list
    drift: list | None  # star drift since previous good frame, report px (dx, dy)
    drift_dt: float | None  # frames (15 min units) the drift was measured over
    star_frames: int  # earlier frames that registered and went into the star model
    n_stars_masked: int
    sun_xy: list | None  # Sun centre, report coords
    warnings: list | None = None  # advisory header flags (frame still used)
    moon_px: int = 0  # pixels masked as the Moon
    bg_span: float | None = None  # 15-min slots spanned by the background frames


def is_warm(m: FrameMeta | None) -> bool:
    return (m is not None and m.ok and m.star_frames >= WARM_STAR_FRAMES
            and m.bg_span is not None and m.bg_span <= WARM_BG_SPAN)


def sig_path(camera: str, name: str) -> Path:
    return derived_dir(camera) / "sig" / (Path(name).stem + ".npy")


def meta_path(camera: str, name: str) -> Path:
    return derived_dir(camera) / "meta" / (Path(name).stem + ".json")


def load_meta(camera: str, name: str) -> FrameMeta | None:
    p = meta_path(camera, name)
    return FrameMeta(**json.loads(p.read_text())) if p.exists() else None


def load_sig(camera: str, name: str) -> np.ndarray:
    return np.load(sig_path(camera, name)).astype(np.float32)


def nanmedian_stack(arrs: list[np.ndarray]) -> np.ndarray:
    """Median over frames, NaN-aware but fast: plain median, then fix the few
    pixels where some (not all) frames were NaN."""
    st = np.stack(arrs)
    m = np.median(st, axis=0)
    fix = np.isnan(m) & np.isfinite(st).any(axis=0)
    if fix.any():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            m[fix] = np.nanmedian(st[:, fix], axis=0)
    return m


def significance(a: np.ndarray) -> np.ndarray:
    d = stars.dog(a)
    return d / stars.robust_noise(d)


def downsample(sig: np.ndarray) -> np.ndarray:
    h, w = sig.shape
    b = sig[: h // 2 * 2, : w // 2 * 2].reshape(h // 2, 2, w // 2, 2)
    n = np.isfinite(b).sum(axis=(1, 3))
    s = np.nansum(b, axis=(1, 3))
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(n >= 2, s / np.sqrt(n), np.nan)
    return out.astype(np.float32)


def sun_report_xy(frame: Frame) -> list | None:
    from astropy.wcs import WCS

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            col, row = (float(v) for v in WCS(frame.header).world_to_pixel_values(0, 0))
        return list(coords.fits_to_report(col, row, frame.data.shape[0]))
    except Exception:
        return None


def _earth_frames(frames: list[Path], lo: int, hi: int) -> set[int]:
    """Indices in [lo - guard, hi + guard) whose header says Earth in the field or eclipse."""
    from astropy.io import fits

    out = set()
    for j in range(max(lo - EARTH_GUARD, 0), min(hi + EARTH_GUARD, len(frames))):
        h = fits.getheader(frames[j], 1)
        if h.get("EARFOV_Q") is False or h.get("E_ECLPSE") not in (0, False, None) \
                or h.get("M_ECLPSE") not in (0, False, None):
            out.add(j)
    return out


class _Pointing:
    """Flags frames whose pointing (CRVAL1/2, deg) jumped from the accepted one."""

    def __init__(self):
        self.ref = None
        self.pending: list[tuple[float, float]] = []

    def check(self, header) -> float | None:
        """None if the pointing is normal, else the jump in arcsec."""
        if "CRVAL1" not in header or "CRVAL2" not in header:
            return None
        p = (float(header["CRVAL1"]), float(header["CRVAL2"]))
        if self.ref is None:
            self.ref = p
            return None
        jump = 3600 * np.hypot(p[0] - self.ref[0], p[1] - self.ref[1])
        if jump <= POINT_JUMP:
            self.ref, self.pending = p, []
            return None
        # Off-pointed, unless the last few off frames all agree: a lasting re-point.
        self.pending = [q for q in self.pending if 3600 * np.hypot(p[0] - q[0], p[1] - q[1]) <= POINT_JUMP] + [p]
        if len(self.pending) >= POINT_REBASE:
            self.ref, self.pending = p, []
            return None
        return jump


class _Diff:
    """A background-subtracted frame plus its point-source catalogue."""

    def __init__(self, frame: Frame, background: np.ndarray):
        self.frame = frame
        self.diff = frame.data - background
        self.background = background
        self.moon_px = mask_moon(self.diff, frame.data, background)
        self.sig = significance(self.diff)
        self.catalog = stars.find_sources(self.sig)


def mask_moon(diff: np.ndarray, data: np.ndarray, background: np.ndarray) -> int:
    """NaN out any big bright blob that isn't in the background: the Moon (or
    another large transient). Returns the number of pixels masked."""
    with np.errstate(invalid="ignore", divide="ignore"):
        bright = np.isfinite(data) & (data > MOON_RATIO * background) & (background > 0)
    if bright.sum() < MOON_MIN_AREA:
        return 0
    lab, n = ndimage.label(bright)
    sizes = np.bincount(lab.ravel())
    sizes[0] = 0
    big = np.nonzero(sizes >= MOON_MIN_AREA)[0]
    if not len(big):
        return 0
    mask = np.isin(lab, big)
    rows, cols = np.nonzero(mask)
    r0, r1 = max(rows.min() - MOON_MARGIN, 0), rows.max() + MOON_MARGIN + 1
    c0, c1 = max(cols.min() - MOON_MARGIN, 0), cols.max() + MOON_MARGIN + 1
    sub = ndimage.binary_dilation(mask[r0:r1, c0:c1], iterations=MOON_MARGIN)
    mask[r0:r1, c0:c1] |= sub
    diff[mask] = np.nan
    return int(mask.sum())


def process_range(camera: str, frames: list[Path], targets: range, dump_stages: set[str] = frozenset(),
                  log=print) -> None:
    """Compute stage-A products for frames[t] for t in targets.

    `frames` is the full ordered list of standard frames; history before the first
    target is read from it as needed. Frames that fail quality checks are left out
    of every background and star model.
    """
    raw: deque[Frame] = deque(maxlen=BG_FRAMES)
    diffs: deque[_Diff] = deque(maxlen=STAR_FRAMES + 1)
    first = max(0, targets.start - (BG_FRAMES + STAR_FRAMES) * 2)  # *2: room for skipped bad frames
    pointing = _Pointing()
    earth = _earth_frames(frames, first, targets.stop)
    for i in range(first, targets.stop):
        p = frames[i]
        wanted = i in targets
        if wanted and meta_path(camera, p.name).exists() and p.name not in dump_stages:
            # Already computed; we still need it in the history buffers for later targets
            # only if a later target is not cached either.
            if all(meta_path(camera, frames[j].name).exists() for j in range(i, targets.stop)):
                return
        f = read_frame(p)
        # Exact zeros are pipeline fill (blobs along the occulter rim and field edge), not sky.
        f.data[f.data == 0] = np.nan
        q = frame_quality(f.header, f.data)
        if q.ok and any(j in earth for j in range(i - EARTH_GUARD, i + EARTH_GUARD + 1)):
            q.ok = False
            q.problems.append("next to an Earth-in-field/eclipse frame (scattered Earthlight)")
        if q.ok:
            jump = pointing.check(f.header)
            if jump is not None:
                q.ok = False
                q.problems.append(f"pointing jumped {jump:.0f} arcsec (CRVAL): calibration off-point")
        if not q.ok:
            if wanted:
                _write_meta(camera, FrameMeta(p.name, f.time.timestamp(), False, q.problems,
                                              None, None, 0, 0, None))
            continue
        if len(raw) < BG_FRAMES:
            raw.append(f)
            if wanted:
                _write_meta(camera, FrameMeta(p.name, f.time.timestamp(), False,
                                              ["not enough history for background"], None, None, 0, 0, None))
            continue
        if i < targets.start - 2 * STAR_FRAMES:
            raw.append(f)  # far enough back that only its raw pixels are needed
            continue
        bg = nanmedian_stack([r.data for r in raw])
        cur = _Diff(f, bg)
        cur.bg_span = (f.time - raw[0].time).total_seconds() / 900.0
        if wanted:
            _finish(camera, cur, list(diffs), dump=p.name in dump_stages, log=log)
        diffs.append(cur)
        raw.append(f)


def _finish(camera: str, cur: _Diff, prev: list[_Diff], dump: bool, log) -> None:
    f = cur.frame
    centre = (f.data.shape[1] / 2 - 0.5, f.data.shape[0] / 2 - 0.5)
    warped, drift, drift_dt = [], None, None
    for k, p in enumerate(reversed(prev), start=1):
        dt = (f.time - p.frame.time).total_seconds() / 900.0
        tr = stars.register(p.catalog, cur.catalog, max_shift=4 * dt + 8, centre=centre)
        if tr is None:
            continue
        if k == 1:
            # report coords: x = col, y = H-1-row  ->  dy flips sign
            drift, drift_dt = [tr.dx / dt, -tr.dy / dt], dt
        warped.append(stars.warp(p.diff, tr))
    problems = []
    if len(warped) >= MIN_STAR_FRAMES:
        model = nanmedian_stack(warped)
        resid = cur.diff - model
        msig = significance(model)
        bright = np.isfinite(msig) & (msig > STAR_MASK_SIGMA)
        yy, xx = np.ogrid[-STAR_MASK_RADIUS:STAR_MASK_RADIUS + 1, -STAR_MASK_RADIUS:STAR_MASK_RADIUS + 1]
        disk = xx ** 2 + yy ** 2 <= STAR_MASK_RADIUS ** 2
        mask = ndimage.binary_dilation(bright, structure=disk)
        n_masked = int(ndimage.label(bright)[1])
        resid = np.where(mask, np.nan, resid)
    else:
        model, mask, n_masked = None, None, 0
        resid = cur.diff
        problems.append(f"star model skipped: only {len(warped)} earlier frames registered")
        log(f"  {f.path.name}: {problems[-1]}")
    sig = significance(resid)
    invalid = ~np.isfinite(cur.diff)
    sig[ndimage.binary_dilation(invalid, iterations=EDGE_GUARD)] = np.nan
    sig = np.clip(sig, -SIG_CLIP, SIG_CLIP)
    small = downsample(sig)
    out = sig_path(camera, f.path.name)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.save(out, small.astype(np.float16))
    warn = frame_quality(f.header).warnings
    _write_meta(camera, FrameMeta(f.path.name, f.time.timestamp(), True, problems, drift, drift_dt,
                                  len(warped), n_masked, sun_report_xy(f), warn, cur.moon_px, cur.bg_span))
    if dump:
        d = derived_dir(camera) / "stages" / f.path.stem
        d.mkdir(parents=True, exist_ok=True)
        stagesd = {"1_raw": f.data, "1_background": cur.background, "1_diff": cur.diff,
                   "2_star_model": model, "2_star_mask": None if mask is None else mask.astype(np.float32),
                   "2_residual": resid, "3_significance": sig, "4_downsampled": small}
        from ..inspect import write_png

        for k, a in stagesd.items():
            if a is None:
                continue
            np.save(d / f"{k}.npy", a)
            write_png(d / f"{k}.png", _to8(a))
        log(f"  stages for {f.path.name} -> {d}")


def _to8(a: np.ndarray) -> np.ndarray:
    """Display stretch for stage dumps, oriented in report coordinates."""
    v = a[np.isfinite(a)]
    if v.size == 0:
        return np.zeros(a.shape, np.uint8)
    lo, hi = np.percentile(v, [1, 99.5])
    img = np.clip((np.nan_to_num(a, nan=lo) - lo) / max(hi - lo, 1e-30), 0, 1)
    return np.flipud((img * 255).astype(np.uint8))


def _write_meta(camera: str, m: FrameMeta) -> None:
    p = meta_path(camera, m.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(m)))
