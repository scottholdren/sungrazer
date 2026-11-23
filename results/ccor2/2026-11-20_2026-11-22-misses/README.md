# CCOR-2 reported comets we did not follow, 2026-11-20 18:00 .. 11-22 12:00 UTC

Four comets were reported on CCOR-2 and not followed by the live watcher (six "reported, not followed
here" cards: Jiangao Ruan reported two of them twice). A scratch replay of stage B and C over the span,
on the VM, compared the live rules with two changes. The unchanged replay reproduces the live result.

| comet | reported by, first post | live reason |
|---|---|---|
| A | Jiangao Ruan, 11-21 03:32 | rejected as pylon (track 20261121T0515_958_1306) |
| B | Jiangao Ruan, 11-21 12:13 | rejected as pylon (track 20261121T1430_1096_1336) |
| C | new user, 11-22 03:28 | never a track: 3-4 sigma a frame, ~7 sigma stacked along its own path |
| D | new user, 11-22 02:25 | rejected as pylon; one of its three positions is dated 11-23 (a typo for 11-22 01:00), which made the miss look like brightness |

## Findings

- **Pylon.** CCOR-2 has no arm band beyond 350 px from the Sun (masked fraction 0-0.3 in every
  direction; CCOR-1 1.0 straight down). In November the CCOR-2 Kreutz lane climbs from straight below
  the Sun, through the pylon strip: all 6 pylon rejections of the span were inbound comets.
- **Peak cap.** Every CCOR-2 stage-B window hit MAX_PEAKS = 400; the weakest kept peak was ~9.1 sigma
  (CCOR-1: ~6.8), and half the kept peaks were in the glare zone or the edge strip, which the tracker
  rejects anyway.

## Replay (times relative to the first report; + = the report came first)

| change | A | B | D | unrejected tracks in 42 h | at alert grade |
|---|---|---|---|---|---|
| none (live) | rejected | rejected | rejected | 0 | 0 |
| no pylon rule | alert +2.8 h | following +3.2 h, never alert | alert +6.5 h | 4 | 2 (A, D) |
| no pylon rule, glare/edge left out before the cap | alert +2.6 h | following +2.2 h, never alert | following -0.8 h, alert +6.5 h | 22 | 2 (A, D) |

Easier alert rules, with both changes: 6 hits 12 sigma: A +2.3, B +4.9, D +5.2 h, 1 other alert;
5 hits 10 sigma: A +2.1, B +3.2, D -0.3 h, 2 other alerts; 8 hits 10 sigma: A +2.8, B +3.4, D +0.7 h,
1 other alert. Not adopted: one comet of four gains.

## Adopted (tracker c9, stage B b4)

- CCOR-2 has no pylon rule (`Camera.pylon`).
- CCOR-2's stage B looks for peaks only outside the glare zone and the edge strip
  (`Camera.peaks_outside_glare`, `stack.searchable`). Tried on CCOR-1 too: on 2026-10-17..24 it doubled
  the unmatched tracks (275 to 560) and found the 10-19 comet 30 min later, for no comet gained, so
  CCOR-1 is unchanged (its scorecard is identical).

A and B stay lost: even with both changes our first detection of A comes 40 min after Jiangao Ruan's
report. Comets that faint would need a deeper search (more frames stacked, a higher cap). C is out of
reach.
