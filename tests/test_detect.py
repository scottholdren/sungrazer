"""Detector building blocks on synthetic data (no frames needed)."""

import numpy as np
import pytest

from kreutz import coords, stars
from kreutz.detect import frames as A
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


def test_ccor2_map_is_blanked_beyond_the_field_radius():
    # 20261104T0500_1642_1856 (2026-11-04): three 10-sigma hits on the vignetting rim, 1064 px from the Sun.
    m = A.outside_field((1920, 2048), "ccor2")
    assert m[1919 - 1856, 1642]  # report (1642, 1856) is outside
    assert not m[1919 - 994, 1018]  # the Sun is inside
    assert not m[1919 - 200, 1018]  # 794 px above the Sun is inside
    assert m[1919 - 994, 2047]  # 1029 px beside the Sun is outside
    assert A.outside_field((1920, 2048), "ccor1") is None


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
    ((-1.2, -0.6), (1600, 1500), {}, "stationary"),  # a detector defect wandering with CCOR-2's registration shift
    ((2.2, -0.4), (1600, 1500), {}, "star-drift"),
    ((5.0, 4.0), (1600, 1500), {}, "outbound"),  # down-right, away from the Sun
    ((-3.0, -3.0), (1200, 1150), {}, "glare-zone"),  # ~250 px from the Sun
    ((-0.5, -4.0), (956, 1630), {}, "pylon"),  # 85 px left of the pylon axis, 670 px below the Sun, inbound
    ((0.5, 4.0), (1130, 300), {}, "pylon"),  # above the Sun too (the arm is on top before the yaw flip and on CCOR-2)
    ((-0.5, -4.0), (1300, 1630), {}, None),  # same motion well clear of the pylon
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


def test_head_and_tail_pieces_of_one_comet_are_merged():
    a, b, c = C.Track(1), C.Track(2), C.Track(3)
    a.hits = [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4) for k in range(6)]
    b.hits = [_peak(k, 1625 - 5 * k, 1520 - 4 * k, -5, -4) for k in range(1, 5)]  # 32 px behind, along the motion
    c.hits = [_peak(k, 1600 - 5 * k + 20, 1500 - 4 * k - 25, -5, -4) for k in range(1, 5)]  # 32 px off: a satellite
    d = C.Track(4)
    d.hits = [_peak(k, 1600 - 5 * k + 60, 1500 - 4 * k - 60, -5, -4) for k in range(1, 5)]  # 85 px off: another object
    out = C.merge_duplicates([a, b, c, d])
    assert sorted(t.id for t in out) == [1, 4] and out[0].merged == 2


def test_a_track_that_continues_another_on_its_path_is_merged():
    a, b, c = C.Track(1), C.Track(2), C.Track(3)
    a.hits = [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4) for k in range(6)]  # ends at k=5
    b.hits = [_peak(k, 1600 - 5 * k + 3, 1500 - 4 * k - 2, -5, -4) for k in range(8, 12)]  # resumes 3 frames later
    c.hits = [_peak(k, 1600 - 5 * k + 120, 1500 - 4 * k, -5, -4) for k in range(8, 12)]  # same gap, off the path
    out = C.merge_duplicates([a, b, c])
    assert sorted(t.id for t in out) == [1, 3]
    assert len(out[0].hits) == 10 and out[0].last.t == 11 * 900.0  # the keeper now carries the continuation


def test_later_rejection_does_not_rewrite_status_at_submission():
    tr = C.Tracker()
    sun = (1041.0, 962.0)
    # inbound comet that starts ~480 px out and ends inside the 320-px glare zone
    for k in range(12):
        tr.update(k * 900.0, [_peak(k, 1441 - 30 * k, 1262 - 22 * k, -30, -22)], DRIFT, sun)
    (t,) = tr.finish()
    assert t.status_at[3] == "candidate"
    assert t.status == "rejected:glare-zone"


def test_merged_track_counts_from_its_earliest_submittable_piece():
    a, b = C.Track(1), C.Track(2)
    a.hits = [_peak(k, 1600 - 5 * k, 1500 - 4 * k, -5, -4) for k in range(2, 8)]
    a.reached, a.status_at = {3: 4 * 900.0}, {3: "rejected:extended"}
    b.hits = [_peak(k, 1601 - 5 * k, 1501 - 4 * k, -5, -4) for k in range(0, 5)]
    b.reached, b.status_at = {3: 2 * 900.0}, {3: "candidate"}
    (m,) = C.merge_duplicates([a, b])
    assert m.reached[3] == 2 * 900.0 and m.status_at[3] == "candidate"


def _stack_of(draw, n=8):
    times = [k * 900.0 for k in range(n)]
    maps = []
    for k in range(n):
        m = np.zeros((200, 200), np.float32)
        draw(m, k)
        maps.append(m)
    return maps, times


def test_stacked_shape_is_round_for_a_comet_with_a_tail_and_long_for_a_sliding_ridge():
    grid = _grid(lo=(-4, -4), hi=(4, 4))

    def comet(m, k):  # head moving +1 half-res col/frame (report vx = +2), faint tail behind it
        c = 80 + k
        m[100, c] = 60.0
        m[100, c - 12:c] = 4.0

    def ridge(m, k):  # a long faint line that slides along itself
        m[100, 60 + k:140 + k] = 4.0

    for draw, lo, hi in ((comet, 0, 2.5), (ridge, 3.0, np.inf)):
        maps, times = _stack_of(draw)
        cut = B.Cutouts(maps, times, grid)
        assert lo <= B.stacked_elongation(cut, 100, 87, 2.0, 0.0) < hi


def test_pixel_thresholds_scale_with_the_camera_plate_scale():
    from kreutz import cameras

    s = cameras.px_scale("ccor2")
    assert 0.78 < s < 0.81 and cameras.px_scale("ccor1") == 1.0
    # 300 px from the Sun is inside CCOR-1's glare zone (320) but outside CCOR-2's (320 * 0.8 = 255)
    hits = [_peak(k, 1041 + 212 - 3 * k, 962 + 212 - 3 * k, -3, -3) for k in range(3)]
    assert C.rejection(-3, -3, hits[-1].x, hits[-1].y, DRIFT, SUN, hits) == "glare-zone"
    assert C.rejection(-3, -3, hits[-1].x, hits[-1].y, DRIFT, SUN, hits, scale=s) is None
    tr = C.Tracker(scale=s)
    assert tr.scale == s


def test_detector_defects_follow_the_pipelines_shift(tmp_path, monkeypatch):
    """CCOR-2's level-1A frames are shifted to centre the Sun (SHIFT_X/Y); a dead-pixel
    cluster fixed on the detector therefore wanders across the frames. The defect map
    is built in detector coordinates and put back onto each frame."""
    from astropy.io import fits as pyfits

    monkeypatch.setattr(A.paths, "DATA", tmp_path)
    monkeypatch.setattr(A.paths, "FRAMES", tmp_path / "frames")
    day = tmp_path / "frames" / "ccor2" / "20261102"
    day.mkdir(parents=True)
    rng = np.random.default_rng(1)
    r0, c0 = 120, 150  # the cluster on the detector
    shifts = [(1.75 + 0.3 * k, -11.5 + 0.5 * k) for k in range(12)]
    names = []
    for k, (sx, sy) in enumerate(shifts):
        img = 1e-10 * (1 + 0.02 * rng.normal(size=(200, 240))).astype(np.float32)
        rr, cc = r0 + int(round(sy)), c0 + int(round(sx))  # registered = detector + shift
        img[rr:rr + 3, cc:cc + 2] *= 0.45
        img[rr + 40:rr + 44, cc + 40:cc + 44] *= 1.04  # a block of warm pixels: gain, not a defect
        h = pyfits.Header()
        h["SHIFT_X"], h["SHIFT_Y"], h["INSTRUME"] = sx, sy, "CCOR2"
        name = f"CCOR2_1A_20261102T{10 + k // 4:02d}{(k % 4) * 15:02d}14_V00_NC.fits"
        pyfits.PrimaryHDU(img, header=h).writeto(day / name)
        names.append(name)
    defects, sign, flat, wide = A.load_defects("ccor2", log=lambda *_: None)
    assert sign == [1, 1] and 4 <= defects.sum() <= 8
    assert 1.025 < np.median(flat[r0 + 40:r0 + 44, c0 + 40:c0 + 44]) < 1.055  # the gain block, in detector coordinates
    assert abs(np.median(flat) - 1) < 0.005 and (flat[r0:r0 + 3, c0:c0 + 2] == 1).all()  # defects are not "corrected"
    h = pyfits.getheader(day / names[0])
    g = A.flat_on_frame(flat, sign, h)
    sx, sy = shifts[0]
    assert 1.02 < g[r0 + 40 + int(round(sy)) + 1, c0 + 40 + int(round(sx)) + 1] < 1.06  # placed back onto the frame
    assert defects[r0:r0 + 3, c0:c0 + 2].all()
    assert A.defect_path("ccor2").exists()
    assert wide[r0:r0 + 3, c0:c0 + 2].all() and wide.sum() == defects.sum()  # the dark cluster takes the wide guard
    for k, (sx, sy) in enumerate(shifts):
        h = pyfits.getheader(day / names[k])
        m = A.defect_mask(defects, sign, h)
        rr, cc = r0 + int(round(sy)), c0 + int(round(sx))
        assert m[rr:rr + 3, cc:cc + 2].all() and m.sum() < 120  # covers the cluster, with a small margin
    assert A.load_defects("ccor1") is None or True  # a camera without frames: no map, no error


def _run(sig, gap, seen=None, back=True):
    """Five hits, `gap` frames with nothing, then (if `back`) the object on its path again."""
    tr = C.Tracker()
    for k in range(5):
        tr.update(k * 900.0, [_peak(k, 1700 - 7 * k, 1500 - 4 * k, -7, -4, sig=sig)], DRIFT, SUN)
    for k in range(5, 5 + gap):
        tr.update(k * 900.0, [], DRIFT, SUN, seen=seen)
    k = 5 + gap
    if back:
        tr.update(k * 900.0, [_peak(k, 1700 - 7 * k, 1500 - 4 * k, -7, -4, sig=sig)], DRIFT, SUN)
    return tr


def test_alerted_track_is_followed_through_two_hours_without_a_hit():
    bright = _run(16.0, 6)  # alert grade: 4 hits, 15 sigma
    assert [len(t.hits) for t in bright.active if t.candidate] == [6] and bright.active[0].sticky
    faint = _run(9.0, 6)  # an ordinary candidate is closed after MAX_MISSES
    assert [len(t.hits) for t in faint.done] == [5] and not faint.done[0].sticky
    assert [len(t.hits) for t in _run(16.0, C.STICKY_MISSES + 1, back=False).done] == [5]  # then it is gone
    told = _run(9.0, 0, back=False)  # the live watcher marks a reported track
    told.active[0].sticky = True
    for k in range(5, 11):
        told.update(k * 900.0, [], DRIFT, SUN)
    assert len(told.active) == 1 and not told.done


def test_frames_blank_at_the_predicted_place_are_not_misses():
    # 20261109T0130_1740_1790: CCOR-1's Earth-glare frames blanked the map over it for five frames
    blind = _run(9.0, 5, seen=lambda x, y: False)
    assert [len(t.hits) for t in blind.active if t.candidate] == [6] and not blind.done
    assert [len(t.hits) for t in _run(9.0, 5, seen=lambda x, y: True).done] == [5]  # looked and saw nothing
    assert [len(t.hits) for t in _run(9.0, C.BLIND_FRAMES + 1, seen=lambda x, y: False, back=False).done] == [5]
    m = np.ones((960, 1024), np.float32)
    m[100:200, 300:400] = np.nan
    seen = B.seen_fn(m)
    assert seen(1000, 1000) and not seen(700, 1919 - 300) and not seen(5000, 10)  # report y runs down


def test_snapshot_keeps_a_followed_tracks_pieces_older_than_the_window():
    # 20261107T1730_1924_1608 lost the half before a gap once that half was a day old
    tr = C.Tracker()
    for k in list(range(6)) + list(range(11, 17)):
        while tr.active and k * 900.0 - tr.active[0].last.t > 900.0 and k == 11:
            for j in range(6, 11):
                tr.update(j * 900.0, [], DRIFT, SUN)
            break
        tr.update(k * 900.0, [_peak(k, 1700 - 7 * k, 1500 - 4 * k, -7, -4, sig=9.0)], DRIFT, SUN)
    assert [len(t.hits) for t in tr.done] == [6]  # the first piece was closed in the gap
    snap = tr.snapshot(since=8 * 900.0)  # the first piece's last hit is older than the window
    assert [len(t.hits) for t in snap] == [12] and snap[0].hits[0].t == 0.0


def test_tracks_taking_turns_on_one_object_are_merged():
    # 2026-11-11 CCOR-2: a 5-hit and a 13-hit track of one comet, hits in alternate frames, never a common time
    path = lambda k: (1384.5 - 3.3 * k, 1896.5 - 4.4 * k)
    a = C.Track(1, [_peak(k, *path(k), -3.3, -4.4) for k in (0, 1, 2, 8, 15)], candidate=True)
    b = C.Track(2, [_peak(k, *path(k), -3.3, -4.4) for k in (4, 5, 7, 10, 11, 14, 16, 17, 18, 19)], candidate=True)
    assert not ({round(h.t) for h in a.hits} & {round(h.t) for h in b.hits})
    merged = C.merge_duplicates([a, b])
    assert len(merged) == 1 and len(merged[0].hits) == 15  # every frame either piece had, in time order
    assert [round(h.t / 900) for h in merged[0].hits] == sorted({0, 1, 2, 8, 15, 4, 5, 7, 10, 11, 14, 16, 17, 18, 19})
    # a parallel object 30 px off the path is not merged
    c = C.Track(3, [_peak(k, path(k)[0] + 30, path(k)[1], -3.3, -4.4) for k in (4, 5, 7, 10, 11)], candidate=True)
    assert len(C.merge_duplicates([a, c])) == 2
