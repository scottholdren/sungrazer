# CCOR-1 candidate 20261112T0030_1688_1866

**9 frames, 2026-11-12 00:30 to 02:30 UTC, peak 238 sigma, brightening.**

- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); status candidate, alert tier since 01:15 UTC.
- Motion (-6.0, -4.3) px per 15-min frame, 7.4 px/frame.
- Last position 1090 px from the Sun at position angle -54 deg, heading 19 deg off the Sun direction (Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, and head within ~20 deg of the Sun).
- No CCOR-1 or CCOR-2 report matches it on the Sungrazer site.

## Movie

A fixed patch of sky at 2x the camera's resolution: significance maps (stars and corona modelled out), the last 8 maps added along the fitted motion, black below 2.5 sigma. The circle sits on the object's measured positions: yellow on a frame with a measured hit, dim and dashed where it is only carried along the path (before the first hit, across a gap, after the last). A real object crosses the patch inside the circle; noise does not travel.

![movie](movie.gif)

## Full field

The object's quadrant from just outside the occulter out to the field edge, up to the newest hit: each frame minus the median of the previous 24 hours (the static corona and glare cancel; streamers show bright where they have moved to and dark where they were; stars stay), no stacking, no smoothing, on one linear scale with zero near black, so the noise shows as grain that thickens toward the Sun. The circle is the same marker as above (yellow on a frame with a measured hit, dim and dashed otherwise), and the movie runs two hours past the last one. This GIF is every frame at half resolution; the dashboard plays them at the camera's resolution. A faint object is easier to judge in the stacked patch above.

![full field](field.gif)

## Contact sheet

Top: raw frame. Middle: significance map of that frame. Bottom: the detector's view, the last 8 maps stacked along the motion. Ticks mark the predicted position; a comet is a steady dot on the ticks.

![contact sheet](sheet.png)

![stacked cutout around the newest hit](thumb.png)

## Positions

2048x1920 full-resolution frame, origin upper left, x right, y down, 0-based (the Sungrazer 'Upper Left' convention). They are the centre of the stacked detection predicted from the fitted motion, good to about a pixel, not per-frame centroids; hunters' measurements tend to fall ~1 px right and up of these.

The last column is what the track became here on that frame: spark (two bright hits, before it is a track), following (third hit), then each of the dashboard's tiers the first time it stood higher than before (steady, watch, ALERT). The owner's tags carry their clock time and sit on the newest frame that had reached us when they were given; the rest are frame times, and a frame reaches us 15 to 25 minutes after it is taken.

| date | HHMM | x | y | sigma | our status |
|---|---|---|---|---|---|
| 2026-11-12 | 0030 | 1688 | 1866 | 10.6 |  |
| 2026-11-12 | 0045 | 1682 | 1862 | 38.8 | spark |
| 2026-11-12 | 0100 | 1676 | 1858 | 68.8 | following |
| 2026-11-12 | 0115 | 1670 | 1854 | 103.2 | ALERT |
| 2026-11-12 | 0130 | 1664 | 1850 | 127.9 |  |
| 2026-11-12 | 0145 | 1658 | 1844 | 163.1 |  |
| 2026-11-12 | 0200 | 1652 | 1840 | 194.8 |  |
| 2026-11-12 | 0215 | 1646 | 1836 | 237.6 |  |
| 2026-11-12 | 0230 | 1640 | 1832 | 231.5 |  |

Form layout (HHMM x y):

```
0030 1688 1866
0045 1682 1862
0100 1676 1858
0115 1670 1854
0130 1664 1850
0145 1658 1844
0200 1652 1840
0215 1646 1836
0230 1640 1832
```

## Movie frames

Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.

| frame | ring | tail | |
|---|---|---|---|
| 20261111T233025 | 0.0 | 5.1 |  |
| 20261111T234525 | 0.0 | 4.6 |  |
| 20261112T000025 | 0.0 | 4.4 |  |
| 20261112T001525 | 9.9 | 4.7 |  |
| 20261112T003025 | 38.0 | 5.3 | hit |
| 20261112T004525 | 60.4 | 6.4 | hit |
| 20261112T010025 | 83.8 | 14.6 | hit |
| 20261112T011525 | 114.9 | 27.4 | hit |
| 20261112T013025 | 150.1 | 38.1 | hit |
| 20261112T014525 | 183.1 | 47.5 | hit |
| 20261112T020025 | 217.6 | 53.1 | hit |
| 20261112T021525 | 252.9 | 62.7 | hit |
| 20261112T023025 | 267.3 | 69.2 | hit |

*Written 2026-11-12 02:51 UTC.*
