"""Detector building blocks on synthetic data (no frames needed)."""

import numpy as np
import pytest

from kreutz import coords, stars
from kreutz.detect import stack as B
from kreutz.detect import track as C
from kreutz.detect.velocity import VelocityGrid

H, W = 1920, 2048


def _grid(step=0.5, lo=(-6, -6), hi=(6, 6)):
    gx, gy = np.meshgrid(np.arange(lo[0], hi[0] + 1e-9, step), np.arange(lo[1], hi[1] + 1e-9, step))
    return VelocityGrid(gx.ravel(), gy.ravel(), np.zeros((1, 2)), 0, step)


def test_shift_and_stack_finds_injected_mover():
    """A 3-sigma-per-frame point moving at report (+4, -3) px/frame stacks up at its newest position."""
    rng = np.random.default_rng(0)
    h, w = H // 2, W // 2  # stage-A maps are half resolution
    x_end, y_end, v = 1200.0, 700.0, (4.0, -3.0)
    maps, times = [], []
    for k in range(8):
        m = rng.normal(size=(h, w)).astype(np.float32)
        dt = 7 - k  # frames before the newest
        x, y = x_end - v[0] * dt, y_end - v[1] * dt
        col, row = coords.report_to_fits(x, y)
        m[int(row) // 2, int(col) // 2] += 3.0 * 2  # half-res pixel holds ~2x the full-res peak
        maps.append(m)
        times.append(k * 900.0)
    grid = _grid()
    best, arg = B.stack_window(maps, times, grid)
    peaks = B.find_peaks(best, arg, np.ones_like(best, bool), times[-1], grid, H)
    top = peaks[0]
    assert abs(top.x - x_end) <= 2 and abs(top.y - y_end) <= 2
    assert (top.vx, top.vy) == pytest.approx(v, abs=0.5)
    assert top.sig > 12


def test_cosmic_ray_is_clipped():
    """A huge one-frame spike can add at most CLIP[1]/sqrt(N) to any stack."""
    maps = [np.zeros((100, 100), np.float32) for _ in range(8)]
    maps[3][50, 50] = 1e4
    best, _ = B.stack_window(maps, [k * 900.0 for k in range(8)], _grid(lo=(-2, -2), hi=(2, 2)))
    assert best.max() == pytest.approx(B.CLIP[1] / np.sqrt(8))


def test_elongation_separates_points_from_lines():
    m = np.zeros((40, 40), np.float32)
    m[20, 20] = m[20, 21] = m[21, 20] = m[21, 21] = 6  # compact blob
    assert B.elongation(m, 20, 20) < 2
    line = np.zeros((40, 40), np.float32)
    for i in range(14, 27):
        line[i, i] = line[i, i + 1] = 6  # thin diagonal streak
    assert B.elongation(line, 20, 20) > C.MAX_ELONGATION


def test_edge_distance_counts_array_border_and_ignores_small_holes():
    m = np.ones((100, 100), np.float32)
    m[40:43, 60:63] = np.nan  # a masked star: small, ignored
    d = B.edge_distance(m)
    assert d[50, 50] == pytest.approx(50, abs=1)  # nearest is the array border
    assert d[0, 50] <= 1
    big = m.copy()
    big[40:70, 40:70] = np.nan  # occulter-sized hole counts
    assert B.edge_distance(big)[50, 30] <= 11


def _peak(t, x, y, vx, vy, sig=10.0, edge=200.0, elong=1.0):
    return B.Peak(t * 900.0, x, y, vx, vy, sig, edge, elong)


SUN = [1041.0, 962.0]
DRIFT = [1.97, -0.27]


def test_tracker_promotes_after_three_consecutive_windows_and_records_thresholds():
    tr = C.Tracker()
    for k in range(6):
        tr.update(k * 900.0, [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4)], DRIFT, SUN)
    (t,) = tr.finish()
    assert t.candidate and t.status == "candidate"
    assert sorted(t.reached) == [3, 4, 5]
    assert t.reached[3] == 2 * 900.0 and t.reached[5] == 4 * 900.0


def test_tracker_does_not_promote_a_two_window_blip():
    tr = C.Tracker()
    for k in range(2):
        tr.update(k * 900.0, [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4)], DRIFT, SUN)
    for k in range(2, 6):
        tr.update(k * 900.0, [], DRIFT, SUN)
    assert tr.finish() == []


@pytest.mark.parametrize("v, pos, kw, why", [
    ((0.3, -0.2), (1600, 1500), {}, "stationary"),
    ((2.2, -0.4), (1600, 1500), {}, "star-drift"),
    ((5.0, 4.0), (1600, 1500), {}, "outbound"),  # down-right, away from the Sun
    ((-3.0, -3.0), (1200, 1150), {}, "glare-zone"),  # ~250 px from the Sun
    ((-5.0, -4.0), (1600, 1500), {"edge": 10.0}, "edge"),
    ((-5.0, -4.0), (1600, 1500), {"elong": 5.0}, "extended"),
    ((-5.0, -4.0), (1600, 1500), {}, None),  # a well-behaved inbound comet
])
def test_rejection_rules(v, pos, kw, why):
    hits = [_peak(k, pos[0] + v[0] * k, pos[1] + v[1] * k, *v, **kw) for k in range(3)]
    assert C.rejection(*v, hits[-1].x, hits[-1].y, DRIFT, SUN, hits) == why


def test_star_registration_recovers_translation_and_rotation():
    rng = np.random.default_rng(3)
    prev = np.column_stack([rng.uniform(100, 1900, 300), rng.uniform(100, 1800, 300), np.ones(300)])
    truth = stars.Transform(2.0, -0.3, np.radians(40 / 3600 * 8), 0, 0.0)
    cur = np.column_stack([truth.apply(prev) + rng.normal(0, 0.3, (300, 2)), np.ones(300)])
    cur[:30, :2] = rng.uniform(100, 1800, (30, 2))  # some sources not in common
    tr = stars.register(prev, cur, max_shift=12)
    assert tr is not None and tr.n_matched > 200
    assert (tr.dx, tr.dy) == pytest.approx((2.0, -0.3), abs=0.1)
    assert tr.theta == pytest.approx(truth.theta, abs=2e-5)


def test_nearby_peaks_from_one_object_start_only_one_track():
    tr = C.Tracker()
    for k in range(5):
        x, y = 1600 - 5 * k, 1500 - 4 * k
        tr.update(k * 900.0, [_peak(k, x, y, -5, -4, sig=12), _peak(k, x + 6, y, -4, -4, sig=9),
                              _peak(k, x, y + 8, -5.5, -3, sig=8)], DRIFT, SUN)
    assert len(tr.finish()) == 1


def test_duplicate_tracks_are_merged():
    a, b = C.Track(1), C.Track(2)
    a.hits = [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4) for k in range(6)]
    b.hits = [_peak(k, 1601 - 5 * k, 1501 - 4 * k, -5, -4) for k in range(2, 5)]
    out = C.merge_duplicates([a, b])
    assert [t.id for t in out] == [1] and out[0].merged == 1
