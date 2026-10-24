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
    assert top.sig > 10  # ~17 for a noiseless 6-sigma dot; sum-minus-max and noise take some


def test_cosmic_ray_adds_nothing():
    """A huge one-frame spike is the max of every stack it touches, so it is dropped."""
    maps = [np.zeros((100, 100), np.float32) for _ in range(8)]
    maps[3][50, 50] = 1e4
    best, _ = B.stack_window(maps, [k * 900.0 for k in range(8)], _grid(lo=(-2, -2), hi=(2, 2)))
    assert best[10:90, 10:90].max() == pytest.approx(0.0)


def test_bright_movers_are_not_capped():
    """Stacked significance keeps growing with brightness (no 22.6 ceiling)."""
    grid = _grid(lo=(-2, -2), hi=(2, 2))
    out = []
    for amp in (10.0, 40.0):
        maps = [np.zeros((100, 100), np.float32) for _ in range(8)]
        for k, m in enumerate(maps):
            m[50, 40 + k] = amp  # +1 half-res px/frame in columns = report vx +2
        best, _ = B.stack_window(maps, [k * 900.0 for k in range(8)], grid)
        out.append(best.max())
    assert out[0] == pytest.approx(10 * 7 / np.sqrt(7))
    assert out[1] == pytest.approx(4 * out[0])


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


def test_tracker_does_not_seed_from_cold_windows_but_keeps_following():
    tr = C.Tracker()
    for k in range(3):
        tr.update(k * 900.0, [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4)], DRIFT, SUN)
    for k in range(3, 6):  # cold: the existing track continues, a new object is ignored
        tr.update(k * 900.0, [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4),
                              _peak(k, 1800 - 5 * k, 1300 - 4 * k, -5, -4)], DRIFT, SUN, seed=False)
    (t,) = tr.finish()
    assert len(t.hits) == 6


def test_pointing_jumps_are_flagged_and_lasting_repoints_accepted():
    from kreutz.detect.frames import POINT_REBASE, _Pointing

    p = _Pointing()
    hdr = lambda a, b: {"CRVAL1": a, "CRVAL2": b}
    assert p.check(hdr(0.158, -0.145)) is None
    assert p.check(hdr(0.1581, -0.1452)) is None  # normal drift, < 60"
    assert p.check(hdr(0.66, 0.91)) > 3000  # calibration off-point
    assert p.check(hdr(-0.17, -1.28)) > 3000  # ...and another, elsewhere
    assert p.check(hdr(0.158, -0.145)) is None  # back home
    results = [p.check(hdr(0.30, -0.145)) for _ in range(POINT_REBASE)]
    assert results[:-1] == [pytest.approx(511, abs=1)] * (POINT_REBASE - 1) and results[-1] is None


def test_quality_rejects_off_sun_pointing():
    from kreutz.fitsio import frame_quality

    assert frame_quality({"OFFSUNDR": 15}).ok
    assert not frame_quality({"OFFSUNDR": 3992}).ok


def test_miss_distance_sign_and_size():
    from kreutz.detect.envelope import miss_distance

    # moving left (-x) along y = 862 (100 px above the Sun on screen): Sun is to the left of the motion
    assert miss_distance(1500, 862, -5, 0, (1041, 962)) == pytest.approx(100)
    assert miss_distance(1500, 1062, -5, 0, (1041, 962)) == pytest.approx(-100)


def test_envelope_from_samples_accepts_its_own_tracks_and_rejects_the_rest():
    from kreutz.detect import envelope as E

    rng = np.random.default_rng(1)
    s = []
    for _ in range(20):  # July-like: lower right, moving up-left, missing the Sun by ~150 px
        vx, vy = rng.uniform(-7, -4), rng.uniform(-6, -4)
        x, y = 1041 + rng.uniform(500, 800), 962 + rng.uniform(300, 500)
        s.append((vx, vy, E.angle_of(x - 1041, y - 962), E.angle_of(vx, vy), E.miss_distance(x, y, vx, vy, (1041, 962))))
    env = E._build(7, False, [7], np.array(s))
    x, y, vx, vy = 1041 + 650, 962 + 400, -5.5, -5.0
    assert env.check(x, y, vx, vy, (1041, 962)) is None
    assert env.check(15, 900, 3.5, 0.0, (1041, 962)) == "off-envelope"  # left-edge entry, moving right
    assert env.check(x, y, -1.4, 0.0, (1041, 962)) == "off-envelope"  # wrong direction
    assert env.check(x, y - 900, vx, vy, (1041, 962)) == "off-envelope"  # parallel but far from the Sun


def test_arc_range_wraps_at_180():
    from kreutz.detect.envelope import ArcRange

    r = ArcRange.of([170, -175, 178], margin=5)
    assert 175 in r and -178 in r and 0 not in r
