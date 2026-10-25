# Status and next steps

Written by hand after each round of runs. The scorecards next to this file are
the numbers; this file is what they mean and what to do next.

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
