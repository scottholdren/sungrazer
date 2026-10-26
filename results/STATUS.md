# Status and next steps

Written by hand after each round of runs. The scorecards next to this file are
the numbers; this file is what they mean and what to do next.

## 2026-10-25 — code 29fb154, stage A a4, stage B b3

Changes since 88ee9ef: pieces of one comet are merged (same velocity within
MATCH_DV, within 40 px along the motion and 8 px across); the shape rule now
measures elongation on the stack at the track's velocity, not on the newest
frame; each track records its status when it reached 3/4/5 hits, and scoring
uses that status, not the final one; leads now include a "real" figure that
adds our latency; report positions are mapped by reporter and evidence (below).

**Latency.** SWPC posts a CCOR-1 FITS file a median of 14.6 min after its
exposure starts (90th percentile 22.6 min; 2358 files, Oct 1-25, from the
listing's timestamps). Polling every 15 min adds 7.5 min on average and
processing 0.5 min, so a detection reaches us about 22.6 min after its frame
time: "real lead" = lead + 0.38 h. The 24 h live watcher's own first-seen
times agree so far (median 23.8 min, 10 files).

### Report coordinates: the 1024x1024 premise didn't hold

- The 11 confirmed reports declared 1024x1024 (jr/zx/rp, Feb-Aug 2025) are
  **full-resolution**. They quote positions up to x=2032, y=1745, which a
  1024 image can't contain. The stack test on their own frames peaks within
  1 px of scale 1 (0.79 vs 0.13 scatter); at scale 2 every position falls off
  the image. They stay on the existing convention.
- Bill Collins's reports (bc, Oct 2026) are what was actually wrong. His view
  is rotated to solar north up (25.0°, about -CROTA) and reduced 2.208x. The
  fitted transform matches 8 positions on two bright tracks at 3.5 px rms;
  fitting either comet alone predicts the other to 5-14 px. It is stored in
  `coords.REPORT_TRANSFORMS`, keyed by reporter and the verified dates
  (Oct 17-24 2026), not by image size, since his crop may change.
- Any other report that declares a small size and quotes no position above
  1023 is **unmapped**. Its positions are dropped from truth, velocity grids
  and envelopes: 45 reports, none confirmed. DB: 1107 full, 2 transformed,
  45 unmapped. The October grid fell from 225 to 196 velocities.
- Side note: at scale 1 hunters' positions sit about 1 px right and up of
  ours (possibly 1-based counting). Too small to matter for matching.

### Benchmark: Jul 17-24 2025 ([scorecard](ccor1/2025-07-17_2025-07-24/scorecard.txt))

All 8 confirmed comets still found, every one live from its 3rd hit. Leads
unchanged; real lead is 0.38 h later.

| Comet | Lead @5 | Real lead @5 | Real lead @3 |
|---|---|---|---|
| 0073 | -0.4 h | -0.0 h | -0.8 h |
| 0074 | -0.9 h | -0.5 h | -1.0 h |
| 0075 | -4.3 h | **-3.9 h** | -4.4 h |
| 0076 | +2.4 h | +2.8 h | +2.3 h |
| 0077 | -0.1 h | +0.3 h | -0.4 h |
| 0078 | -1.5 h | -1.2 h | -1.9 h |
| 0079 | -0.6 h | -0.2 h | -0.7 h |
| 0080 | +1.4 h | +1.8 h | +1.3 h |

Counting real leads, we'd still be first on 6 of 8 at 3 hits and 5 of 8 at 5.

| Threshold | Precision (was) | Excluding SOHO 5217 dates (was) |
|---|---|---|
| 3 hits | 8/133 = 0.06 (0.04) | 8/33 = 0.24 (0.12) |
| 4 hits | 8/53 = 0.15 (0.09) | 8/16 = 0.50 (0.24) |
| 5 hits | 8/27 = 0.30 (0.16) | 8/15 = 0.53 (0.33) |

- What's left is mostly one bright comet. 84 of the 133 unmatched tracks that
  reach 3 hits lie within 60 px of a bright live track at the same time. The
  SOHO 5217 comet is tracked as four consecutive pieces
  (76837 → 79510 → 83392 → 88227, each starting within 15 min of the last one
  ending), with dozens of short side tracks around them. The merge only joins
  tracks that overlap in time, so these stay separate.
- fp_9847 and fp_57491 (possible unreported comets) are still there and still
  compact (stacked elongation 1-2). Not yet vetted.

### Recent week: Oct 17-24 2026, provisional ([scorecard](ccor1/2026-10-17_2026-10-24/scorecard.txt))

Truth is still hunter claims; NRL hasn't posted confirmations since Aug 2025.

- **All 5 claims matched** (was 3/5). The two former misses were bc's:
  - Oct 19 (track 18983, 264 sigma, 52 hits, 3 pieces merged): live from
    Oct 19 14:45, real lead **-12.5 h** at 3 hits. Under the old shape rule this
    comet was rejected as "extended".
  - Oct 23 is the same comet cw claimed 6 h earlier (track 71519, 8 pieces
    merged). Against cw, our real lead is +0.6 h at 3 hits; the -5.1 h
    against bc's own post isn't a real lead.
- jr Oct 21: at 3 hits the track was off-envelope; it became a candidate at
  4 hits (real lead +1.8 h). Status-at-k scoring shows this; final-status
  scoring would have hidden it.

| Threshold | Live tracks (was) | Precision |
|---|---|---|
| 3 hits | 293 (365) | 0.01 |
| 4 hits | 144 (192) | 0.03 |
| 5 hits | 72 (113) | 0.06 |

- The streamer near (1370, 1250) is only partly handled. Stacked elongation
  rejects 14 tracks there, but 13 still reach 5 hits. Tracks starting around
  x 1300-1500, y 1100-1200 make up about half of the 72 at 5 hits. The rule
  keeps every real comet (stacked elongation 1.0-2.2 against 2.6-10.8 on the
  streamer tracks we measured), so MAX_ELONGATION = 3.0 could come down to
  about 2.4. That needs checking on more comets first.
- The brightest unmatched tracks (30208-30211, 27860, 77339, 79839...) are the
  two bright comets again: continuations after the main track ends (18983
  stops at Oct 20 03:30; 30208 picks up at 06:15 on its path), or tail pieces
  at a different velocity. 39 of the 293 lie within 60 px of a bright live
  track.

### Next steps

1. **Link consecutive pieces:** join a track that starts where another
   same-velocity track ended, within a few frames (fixes SOHO 5217 and 18983's
   continuation).
2. **Suppress satellites of a bright comet:** drop short tracks within ~60 px
   of a live track that is much brighter at the same time. On these runs that
   removes 84 of 133 July FPs at 3 hits and 39 of 293 in October; check it
   doesn't remove a second comet passing nearby.
3. **Streamer:** tighten MAX_ELONGATION toward 2.4 after measuring more
   confirmed comets, and/or reject tracks that slide along a radial ray.
4. Review items not yet done: 1 (allow one miss before candidacy), 3
   (GLARE_RADIUS), 6 (centroid), 7 (prune grid), 8 (chance floor), 9.
5. **Vet fp_9847 and fp_57491** as unreported comets.
6. Re-score October when NRL posts confirmations; re-score after the live
   watcher's 24 h for more first-seen lag data.

## 2026-10-24 — code 88ee9ef, stage A a4, stage B b2

### Benchmark: Jul 17-24 2025 ([scorecard](ccor1/2025-07-17_2025-07-24/scorecard.txt))

All 8 confirmed comets found. Lead = our 5-hit time minus the first human
report; negative means we'd have been first.

| Comet | Lead @5 hits | Best sig | Before these fixes |
|---|---|---|---|
| 0073 | -0.4 h | 31.5 | +0.8 h |
| 0074 | -0.9 h | 38.1 | -0.9 h |
| 0075 | **-4.3 h** | 9.7 | missed |
| 0076 | +2.4 h | 22.5 | +3.2 h |
| 0077 | -0.1 h | 39.2 | +0.4 h |
| 0078 | -1.5 h | 25.8 | +0.2 h |
| 0079 | -0.6 h | 16.1 | -0.4 h |
| 0080 | +1.4 h | 251.4 | +1.4 h |

- Precision at 5 hits: 8/50 (0.16). Leaving out Jul 23-25, where SOHO 5217 (a
  bright comet nobody reported on CCOR-1) makes 151 of the unmatched tracks:
  8/24 (0.33); it was 7/40 before.
- Significance no longer saturates at 22.6; comets range 9.7-251 sigma.
- Possible comets nobody reported on CCOR-1, both inside the July envelope:
  [fp_9847](ccor1/2025-07-17_2025-07-24/sheets/fp_9847.png) (formerly 10766;
  12 hits, 28.8 sigma) and
  [fp_57491](ccor1/2025-07-17_2025-07-24/sheets/fp_57491.png) (Jul 22 04:00,
  (1504, 1144), 17 hits). Not yet vetted.

### Recent week: Oct 17-24 2026, provisional ([scorecard](ccor1/2026-10-17_2026-10-24/scorecard.txt))

Truth here is hunter claims, not confirmations: NRL hasn't posted a
confirmation list since Aug 2025. Re-score when it does (`kreutz reports sync`,
then `kreutz score`).

- 3 of 5 claims matched.
- The two misses (bc, Oct 19 and Oct 23) were reported on 1024x1024 images
  with a "Lower Right" origin. The parser flips the origin but doesn't rescale
  to CCOR's 2048x1920, so their positions are wrong, and no simple rescale
  lines up with any track. Not a detector failure.
- [fp_30154](ccor1/2026-10-17_2026-10-24/sheets/fp_30154.png) is a real, bright
  comet with a tail on Oct 20, very likely the one bc reported.
- 113 tracks reach 5 hits. Most fall into two groups:
  - bright tailed comets split into pieces (30154/30155/30156 are one comet;
    the pieces are ~26 px apart along the tail, beyond the duplicate merge);
  - a streamer pattern near (1370, 1250) that slides at comet-like speeds every
    day and isn't caught by the elongation rule
    ([fp_4621](ccor1/2026-10-17_2026-10-24/sheets/fp_4621.png)).
- The warm-up gate removed the 10:15 artifact. The star-drift, stationary and
  edge rules now catch almost nothing in October because the October velocity
  grid has no slow velocities.

### Next steps

1. **1024x1024 report coordinates.** 11 confirmed and 20 pending Kreutz reports
   use this size; their wrong positions also feed the velocity grids and
   envelopes. Proposal: work out the mapping with the shift-and-stack check
   that settled the original convention, on the 11 confirmed reports; until
   then, leave non-2048x1920 reports out of truth, grids and envelopes.
   **Waiting on the owner:** this extends the coordinate convention, which
   was declared settled.
2. **Merge pieces of one comet:** join tracks moving at the same velocity even
   when tens of px apart along the tail.
3. **Streamer rejection:** measure elongation on the stacked image rather than
   single frames, or reject tracks whose position slides along a radial ray;
   check it keeps every real comet.
4. **Vet fp_9847 and fp_57491** as unreported comets.
5. **Re-score October** when NRL posts confirmations.
