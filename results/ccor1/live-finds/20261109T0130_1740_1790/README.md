# CCOR-1 candidate 20261109T0130_1740_1790

**2 frames, 2026-11-09 01:30 to 01:45 UTC, peak 10 sigma, steady.**

- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); status spark.
- Motion (-6.5, -4.5) px per 15-min frame, 7.9 px/frame.
- Last position 1112 px from the Sun at position angle -49 deg, heading 14 deg off the Sun direction (Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, and head within ~20 deg of the Sun).
- No CCOR-1 or CCOR-2 report matches it on the Sungrazer site.

## Movie

A fixed patch of sky at 2x the camera's resolution: significance maps (stars and corona modelled out), the last 8 maps added along the fitted motion, black below 2.5 sigma. The ring rides the fitted path: green on frames with a measured hit, yellow before the first hit. A real object crosses the patch inside the ring; noise does not travel.

![movie](movie.gif)

## Full field

The object's quadrant at half resolution, from just outside the occulter out to the field edge, the last frames up to the newest hit: each frame minus the minimum of the previous 24 hours (streamers and stars stay, the static corona and glare cancel), lightly smoothed, no stacking, on a linear scale from -2 sigma of the frame's noise (so the noise shows as grain). The crosshair follows the fitted path, yellow on a measured hit. For where the object sits in the corona and what else is moving; a faint object is easier to judge in the stacked patch above.

![full field](field.gif)

![stacked cutout around the newest hit](thumb.png)

## Positions

2048x1920 full-resolution frame, origin upper left, x right, y down, 0-based (the Sungrazer 'Upper Left' convention). They are the centre of the stacked detection predicted from the fitted motion, good to about a pixel, not per-frame centroids; hunters' measurements tend to fall ~1 px right and up of these.

| date | HHMM | x | y | sigma |
|---|---|---|---|---|
| 2026-11-09 | 0130 | 1740 | 1790 | 8.0 |
| 2026-11-09 | 0145 | 1734 | 1786 | 9.9 |

Form layout (HHMM x y):

```
0130 1740 1790
0145 1734 1786
```

## Movie frames

Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.

| frame | ring | tail | |
|---|---|---|---|
| 20261109T003025 | 1.5 | 5.0 |  |
| 20261109T004525 | 3.5 | 5.1 |  |
| 20261109T010025 | 4.8 | 4.8 |  |
| 20261109T011525 | 6.7 | 4.8 |  |
| 20261109T013025 | 7.7 | 4.4 | hit |
| 20261109T014525 | 10.4 | 4.9 | hit |

*Written 2026-11-09 02:16 UTC.*
