# CCOR-1 candidate 20261122T0800_1654_1858

**8 frames, 2026-11-22 08:00 to 09:45 UTC, peak 15 sigma, brightening.**

- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); status candidate, alert tier since 09:45 UTC.
- Motion (-5.1, -4.8) px per 15-min frame, 7.0 px/frame.
- Last position 1072 px from the Sun at position angle -55 deg, heading 12 deg off the Sun direction (Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, and head within ~20 deg of the Sun).
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
| 2026-11-22 | 0800 | 1654 | 1858 | 7.1 |  |
| 2026-11-22 | 0815 | 1650 | 1856 | 7.1 |  |
| 2026-11-22 | 0830 | 1642 | 1850 | 9.2 | following |
| 2026-11-22 | 0845 | 1638 | 1844 | 12.1 |  |
| 2026-11-22 | 0900 | 1634 | 1840 | 12.4 | watch |
| 2026-11-22 | 0915 | 1628 | 1836 | 12.9 |  |
| 2026-11-22 | 0930 | 1624 | 1830 | 12.9 |  |
| 2026-11-22 | 0945 | 1618 | 1826 | 15.3 | ALERT |

Form layout (HHMM x y):

```
0800 1654 1858
0815 1650 1856
0830 1642 1850
0845 1638 1844
0900 1634 1840
0915 1628 1836
0930 1624 1830
0945 1618 1826
```

## Movie frames

Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.

| frame | ring | tail | |
|---|---|---|---|
| 20261122T070025 | 3.2 | 4.5 |  |
| 20261122T071525 | 5.1 | 4.7 |  |
| 20261122T073025 | 4.7 | 4.7 |  |
| 20261122T074525 | 5.8 | 4.5 |  |
| 20261122T080025 | 6.9 | 5.6 | hit |
| 20261122T081525 | 8.2 | 5.1 | hit |
| 20261122T083025 | 8.1 | 5.2 | hit |
| 20261122T084525 | 9.4 | 5.4 | hit |
| 20261122T090025 | 11.7 | 5.7 | hit |
| 20261122T091525 | 11.9 | 5.5 | hit |
| 20261122T093025 | 14.6 | 5.8 | hit |
| 20261122T094525 | 16.8 | 5.7 | hit |

*Written 2026-11-22 10:10 UTC.*
