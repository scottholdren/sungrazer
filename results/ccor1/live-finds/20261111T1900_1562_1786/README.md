# CCOR-1 candidate 20261111T1900_1562_1786

**8 frames, 2026-11-11 19:00 to 20:45 UTC, peak 11 sigma, steady.**

- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); status candidate, watch tier since 20:00 UTC.
- Motion (-6.0, -5.1) px per 15-min frame, 7.9 px/frame.
- Last position 953 px from the Sun at position angle -57 deg, heading 17 deg off the Sun direction (Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, and head within ~20 deg of the Sun).
- Matches Chris Wage's CCOR-1 report `report_cw_20261111160443` (posted 2026-11-11T21:04+00:00; we never alerted on it; we were following it 1.2 h before the post).

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
| 2026-11-11 | 1900 | 1562 | 1786 | 9.1 |  |
| 2026-11-11 | 1915 | 1556 | 1780 | 10.9 | spark |
| 2026-11-11 | 1930 | 1550 | 1776 | 8.6 | following |
| 2026-11-11 | 1945 | 1544 | 1772 | 9.1 |  |
| 2026-11-11 | 2000 | 1538 | 1764 | 9.6 | watch |
| 2026-11-11 | 2015 | 1532 | 1760 | 10.5 |  |
| 2026-11-11 | 2030 | 1526 | 1758 | 7.2 |  |
| 2026-11-11 | 2045 | 1520 | 1748 | 7.9 |  |

Form layout (HHMM x y):

```
1900 1562 1786
1915 1556 1780
1930 1550 1776
1945 1544 1772
2000 1538 1764
2015 1532 1760
2030 1526 1758
2045 1520 1748
```

## Movie frames

Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.

| frame | ring | tail | |
|---|---|---|---|
| 20261111T180025 | 6.9 | 5.0 |  |
| 20261111T181525 | 6.5 | 5.1 |  |
| 20261111T183025 | 8.9 | 5.0 |  |
| 20261111T184525 | 9.0 | 5.0 |  |
| 20261111T190025 | 10.2 | 5.2 | hit |
| 20261111T191525 | 10.6 | 5.3 | hit |
| 20261111T193025 | 10.6 | 5.4 | hit |
| 20261111T194525 | 10.4 | 5.3 | hit |
| 20261111T200025 | 11.6 | 5.3 | hit |
| 20261111T201525 | 12.4 | 5.2 | hit |
| 20261111T203025 | 11.2 | 5.1 | hit |
| 20261111T204525 | 11.9 | 5.1 | hit |
| 20261111T210025 | 9.5 | 5.1 |  |
| 20261111T211525 | 8.3 | 5.1 |  |
| 20261111T213025 | 7.8 | 4.9 |  |
| 20261111T214525 | 7.1 | 5.3 |  |
| 20261111T220025 | 7.3 | 5.0 |  |
| 20261111T221525 | 7.8 | 5.5 |  |
| 20261111T223025 | 8.0 | 5.8 |  |
| 20261111T224525 | 10.7 | 5.7 |  |

*Written 2026-11-12 17:02 UTC.*
