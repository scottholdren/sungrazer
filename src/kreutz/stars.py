"""Stars: find bright point sources and work out how the star field moved.

Seen from GOES-19 the Sun moves ~1 degree/day against the stars, so in a
Sun-centred image every star drifts together by ~2 px per 15-minute frame. We
measure that drift (a translation plus a tiny rotation) between frames by
matching point-source catalogues, then use it to line up earlier frames on the
current one and build a "stars only" model to subtract.

All functions are NaN-aware: ~11% of every frame (occulter, arm, edges) is NaN.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

CATALOG_SIZE = 300  # brightest point sources per frame used for registration
MIN_MATCHES = 20  # fewer matched pairs than this = registration failed


def nan_gaussian(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur that ignores NaNs (normalised convolution); NaN stays NaN."""
    valid = np.isfinite(a)
    num = ndimage.gaussian_filter(np.where(valid, a, 0).astype(np.float32), sigma, mode="constant")
    den = ndimage.gaussian_filter(valid.astype(np.float32), sigma, mode="constant")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[~valid] = np.nan
    return out


def dog(a: np.ndarray, core: float = 1.2, surround: float = 4.0) -> np.ndarray:
    """Difference of Gaussians: keeps blobs ~2-3 px across, removes smooth light."""
    return nan_gaussian(a, core) - nan_gaussian(a, surround)


def robust_noise(a: np.ndarray, block: int = 64) -> np.ndarray:
    """Local noise level (1.4826 * median |x - median|) in blocks, smoothly upsampled."""
    h, w = a.shape
    nb_y, nb_x = h // block, w // block
    blocks = a[: nb_y * block, : nb_x * block].reshape(nb_y, block, nb_x, block).swapaxes(1, 2)
    blocks = blocks.reshape(nb_y, nb_x, -1)
    with np.errstate(all="ignore"), _quiet():
        med = np.nanmedian(blocks, axis=2)
        mad = 1.4826 * np.nanmedian(np.abs(blocks - med[..., None]), axis=2)
    # Blocks that are mostly NaN: borrow from neighbours.
    good = np.isfinite(mad) & (mad > 0)
    if good.any():
        idx = ndimage.distance_transform_edt(~good, return_distances=False, return_indices=True)
        mad = mad[tuple(idx)]
    else:
        mad = np.ones_like(mad)
    full = ndimage.zoom(mad, (h / nb_y, w / nb_x), order=1)
    return full[:h, :w].astype(np.float32)


class _quiet:
    def __enter__(self):
        import warnings

        self._w = warnings.catch_warnings()
        self._w.__enter__()
        warnings.simplefilter("ignore", RuntimeWarning)

    def __exit__(self, *exc):
        self._w.__exit__(*exc)


def find_sources(sig: np.ndarray, n: int = CATALOG_SIZE, threshold: float = 5.0,
                 edge: int = 12) -> np.ndarray:
    """(n, 3) array of (col, row, significance) for the strongest local maxima.

    `edge` keeps away from NaN regions (occulter rim and field edge glow badly).
    """
    s = np.where(np.isfinite(sig), sig, -np.inf)
    near_nan = ndimage.binary_dilation(~np.isfinite(sig), iterations=edge)
    peaks = (s == ndimage.maximum_filter(s, size=5)) & (s > threshold) & ~near_nan
    rows, cols = np.nonzero(peaks)
    vals = s[rows, cols]
    order = np.argsort(vals)[::-1][:n]
    return np.column_stack([cols[order], rows[order], vals[order]]).astype(np.float64)


@dataclass
class Transform:
    """Maps a point in an earlier frame to the current one: p' = R(theta) p + t,
    rotation about the frame centre. Units: FITS array pixels (col, row)."""
    dx: float
    dy: float
    theta: float  # radians
    n_matched: int
    rms: float
    centre: tuple[float, float] = (1023.5, 959.5)

    def apply(self, pts: np.ndarray) -> np.ndarray:
        c = np.asarray(self.centre)
        ct, st = np.cos(self.theta), np.sin(self.theta)
        p = pts[:, :2] - c
        return np.column_stack([ct * p[:, 0] - st * p[:, 1], st * p[:, 0] + ct * p[:, 1]]) + c + [self.dx, self.dy]


def register(prev: np.ndarray, cur: np.ndarray, max_shift: float, centre=(1023.5, 959.5)) -> Transform | None:
    """Find the star-field motion from catalogue `prev` to catalogue `cur`.

    1. Vote: every pairwise offset within max_shift goes into a 1-px histogram;
       stars all moved the same way, so the true offset is the tallest bin.
    2. Refine: keep pairs within 2 px of that offset (one partner each) and fit
       rotation + translation by least squares.
    Returns None if fewer than MIN_MATCHES stars agree.
    """
    if len(prev) < MIN_MATCHES or len(cur) < MIN_MATCHES:
        return None
    d = cur[None, :, :2] - prev[:, None, :2]  # (n_prev, n_cur, 2)
    close = np.hypot(d[..., 0], d[..., 1]) <= max_shift
    if close.sum() < MIN_MATCHES:
        return None
    off = d[close]
    edges = np.arange(-np.ceil(max_shift) - 0.5, np.ceil(max_shift) + 1.5, 1.0)
    hist, xe, ye = np.histogram2d(off[:, 0], off[:, 1], bins=[edges, edges])
    hist = ndimage.uniform_filter(hist, 3, mode="constant")  # true offset may straddle bins
    ix, iy = np.unravel_index(np.argmax(hist), hist.shape)
    guess = np.array([(xe[ix] + xe[ix + 1]) / 2, (ye[iy] + ye[iy + 1]) / 2])

    err = np.hypot(d[..., 0] - guess[0], d[..., 1] - guess[1])
    pairs = []
    used_cur = set()
    for i in np.argsort(err.min(axis=1)):
        j = int(np.argmin(err[i]))
        if err[i, j] <= 2.5 and j not in used_cur:
            pairs.append((i, j))
            used_cur.add(j)
    if len(pairs) < MIN_MATCHES:
        return None
    a = prev[[i for i, _ in pairs], :2] - centre
    b = cur[[j for _, j in pairs], :2] - centre
    # 2-D Procrustes (rotation + translation)
    am, bm = a.mean(0), b.mean(0)
    a0, b0 = a - am, b - bm
    theta = np.arctan2((a0[:, 0] * b0[:, 1] - a0[:, 1] * b0[:, 0]).sum(),
                       (a0 * b0).sum())
    ct, st = np.cos(theta), np.sin(theta)
    rot_am = np.array([ct * am[0] - st * am[1], st * am[0] + ct * am[1]])
    t = bm - rot_am
    tr = Transform(float(t[0]), float(t[1]), float(theta), len(pairs), 0.0, tuple(centre))
    resid = tr.apply(prev[[i for i, _ in pairs]]) - cur[[j for _, j in pairs], :2]
    tr.rms = float(np.sqrt((resid ** 2).sum(1).mean()))
    return tr


def warp(a: np.ndarray, tr: Transform) -> np.ndarray:
    """Resample earlier frame `a` onto the current frame's pixel grid (NaN-aware, bilinear)."""
    c = np.asarray(tr.centre)
    ct, st = np.cos(tr.theta), np.sin(tr.theta)
    # Output pixel q (col,row) came from p = R^-1 (q - c - t) + c.
    # ndimage works in (row, col) order.
    rinv = np.array([[ct, st], [-st, ct]])  # inverse rotation, (col,row) basis
    m_rc = rinv[::-1, ::-1]  # same matrix in (row, col) basis
    t_cr = np.array([tr.dx, tr.dy])
    offset_cr = c - rinv @ (c + t_cr)
    offset_rc = offset_cr[::-1]
    valid = np.isfinite(a)
    num = ndimage.affine_transform(np.where(valid, a, 0).astype(np.float32), m_rc, offset_rc,
                                   order=1, mode="constant", cval=0.0)
    den = ndimage.affine_transform(valid.astype(np.float32), m_rc, offset_rc,
                                   order=1, mode="constant", cval=0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(den > 0.99, num / den, np.nan)
    return out.astype(np.float32)
