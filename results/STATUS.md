# Status and next steps

Written by hand after each round of runs. The scorecards next to this file are
the numbers; this file is what they mean and what to do next.

## 2026-10-26 — code 42062a2, stage A a4, stage B b3: live on the VM, and two lead-time wins

The detector now runs continuously on a Proxmox VM (`kreutz watch` under
systemd, dashboard `kreutz serve` on http://192.168.4.39:8080/). A cycle
takes ~30 s: resident stage A (7 s per new frame), persisted tracker, polls
at 2 min past each 15-min slot (expected wait after SWPC posts a frame
5.1 min). Reports (CCOR-1 plus LASCO C2/C3 for time coincidence) are synced
every cycle. The dashboard shows one card per object (tracks within 60 px
at the same time or continuing a path are grouped), a live activity feed,
and a detail view that animates each track's frames.

### Two changes to the detector, both scored on both weeks

1. **Tracks may start from windows with cold frames** (`detect.SEED_COLD`).
   The gate was added for slow post-gap artefacts that the per-month grid can
   no longer stack (October grid: vx -12.5..-6).
2. **Windows shrink after a gap**: `window_members` drops its oldest frames
   until the span fits 14 slots, down to 6 frames, and counts the span in
   whole slots (seconds of jitter made 3.5 h read as 14.0004 > 14). On Oct 23
   the first usable window after the 06:15-07:45 off-point moves from 09:45
   to 08:45.

Oct 17-24 2026 (provisional truth, latency 22.6 min), real lead at 3 hits:

| claim | first post | before | after |
|---|---|---|---|
| bc Oct 19 | 10-20 03:38 | -12.5 h | -12.5 h |
| jr Oct 21 | 10-21 11:51 | +1.8 h (4 hits; off-envelope at 3) | **-2.0 h** |
| jr Oct 22 | 10-22 13:20 | +0.5 h | +0.5 h |
| **cw Oct 23 (Chris Wage, 264 sigma)** | 10-23 13:16 | +0.6 h | **-3.9 h** |
| bc Oct 23 (same comet) | 10-23 19:01 | -5.1 h | -9.6 h |

Live tracks at 3/4/5 hits: 293/144/72 → 411/188/96 (+40% at 3 hits);
recall 5/5 at every threshold. July 17-24 2025: identical before and after
(8/8 found, 133/53/27 live tracks; that week has almost no cold windows).

Why Chris's comet still takes until 09:00: it entered the field during the
off-point (expected x > 2048 at 06:00); frames are good from 08:00 and the
stack peak is 120-137 sigma in every window from the first usable one, so
the remaining 15 min after 08:45 is the three windows a candidate needs.

3. **The tracker merges satellites and continuations** (track.py c2): a
   shorter track within 60 px of a longer one at two common times is a
   piece of it; a track starting within 3 frames after another ends, on its
   predicted path at its velocity, continues it. Scored after 1 and 2:

   | | Oct 17-24 live at 3/4/5 hits | July 3/4/5 | July precision @3 / @5 |
   |---|---|---|---|
   | before | 411 / 188 / 96 | 133 / 53 / 27 | 0.06 / 0.30 |
   | after | **288 / 149 / 78** | **43 / 20 / 18** | **0.19 / 0.44** |

   Leads and recall unchanged; the SOHO 5217 swarm in July goes from 107
   tracks to 13. Precision excluding those dates: 8/30 at 3 hits.

### Live findings (Oct 24-26)

- `20261024T2145_1662_1282`: 57 sigma, 28 hits, Oct 24 21:45 → Oct 25 06:30,
  into the glare zone at (1342,1160); compact in every tile; **unreported on
  CCOR-1, C2 and C3**. Vetted by eye only.
- `20261025T0730_1736_1310`: 58 hits over 14 h at 20 sigma with no misses.
  Nothing visible in the raw tiles; probably sliding structure. Unvetted.
- Precision is still ~1%: ~400 live tracks a week in October, 17 objects on
  the dashboard at any time. The bright comets are unmistakable (Chris's was
  264 sigma); the list is the problem for anything faint.

Tried and rejected: MAX_ELONGATION 2.4 (from 3.0). October live tracks
288 -> 260 at 3 hits, but CCR1 0077 (July) is rejected as extended at its
5th hit (recall 7/8 at 5 hits). It stays at 3.0.

Tried and rejected: review item 1, one missed window allowed before
candidacy. October live tracks 288 -> 380 at 3 hits (+32%) for 15 min on
one comet per week (jr Oct 22, CCR1 0073); CCR1 0075, the faint one it was
meant to help, is unchanged.

Review item 8, the chance floor: the tracker run on the same windows with
the peaks of each window drawn from a random other window of the week
(per-window peak statistics kept, real continuity broken) makes **1 live
track per week** (3 trials) against 288 real. The ~290 unmatched tracks are
moving structure, not coincidence; MAX_PEAKS and PEAK_THRESHOLD are not the
lever, per-track discriminators are.

Per-track features on both weeks' live tracks: at 3 hits nothing separates
the faint comets (7.5-8.3 sigma, elongation 1.7, straight) from the rest.
What does is persistence: confirmed comets run 7-84 hits (median ~50),
unmatched tracks die at a median of 4-5. So the posting rule is "k hits and
best significance >= s", evaluated as it would have fired in real time:

| rule | Oct 17-24: fires / comets / precision / median real lead | Jul 17-24 |
|---|---|---|
| 3 hits | 295 / 4 of 5 / 0.01 / -2.9 h | 46 / 8 of 8 / 0.17 / -0.8 h |
| **alert: 4 hits, >= 15 sigma** | 5 / 3 of 5 / 0.60 / -3.6 h | 18 / 7 of 8 / 0.39 / +1.8 h |
| **watch: 5 hits, >= 10 sigma** | 18 / 3 of 5 / 0.17 / -3.4 h | 22 / 7 of 8 / 0.32 / +0.2 h |

The October misses at either tier are the two faint comets (8 and 15 sigma
peak). The dashboard shows the tiers (`live.TIERS`), sorts alerts first and
logs ALERT events; Discord posting hooks on those when a webhook exists.

### Next steps

1. Discord: post ALERT (and maybe watch) events when the owner provides a
   webhook URL.
2. A second CCOR-1 week with confirmations (Aug 2025) to check the tiers
   generalise.
3. Discord alerts for new unreported objects (webhook not yet provided).
4. Re-score October when NRL posts confirmations.

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
