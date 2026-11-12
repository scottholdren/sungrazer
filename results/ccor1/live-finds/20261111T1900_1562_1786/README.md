# CCOR-1 candidate 20261111T1900_1562_1786

**51 frames, 2026-11-11 19:00 to 2026-11-12 10:15 UTC, peak 16 sigma, steady.**

- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); status candidate, alert tier since 02:45 UTC.
- Motion (-6.2, -5.6) px per 15-min frame, 8.4 px/frame.
- Last position 525 px from the Sun at position angle -70 deg, heading 28 deg off the Sun direction (Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, and head within ~20 deg of the Sun).
- Matches Chris Wage's CCOR-1 report `report_cw_20261111160443` (posted 2026-11-11T21:04+00:00; it came 6.1 h before our alert; we were following it 1.2 h before the post).

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
| 2026-11-11 | 2215 | 1482 | 1718 | 7.1 |  |
| 2026-11-11 | 2230 | 1476 | 1714 | 8.8 |  |
| 2026-11-11 | 2245 | 1472 | 1708 | 8.4 |  |
| 2026-11-11 | 2300 | 1466 | 1704 | 10.0 |  |
| 2026-11-11 | 2315 | 1460 | 1700 | 8.0 |  |
| 2026-11-11 | 2330 | 1452 | 1694 | 8.8 |  |
| 2026-11-11 | 2345 | 1446 | 1686 | 8.9 |  |
| 2026-11-12 | 0000 | 1440 | 1684 | 9.3 |  |
| 2026-11-12 | 0015 | 1434 | 1676 | 10.9 |  |
| 2026-11-12 | 0030 | 1428 | 1674 | 9.2 |  |
| 2026-11-12 | 0045 | 1422 | 1666 | 10.9 |  |
| 2026-11-12 | 0100 | 1416 | 1662 | 11.6 |  |
| 2026-11-12 | 0115 | 1408 | 1656 | 12.8 |  |
| 2026-11-12 | 0130 | 1402 | 1650 | 11.9 |  |
| 2026-11-12 | 0145 | 1396 | 1644 | 11.9 |  |
| 2026-11-12 | 0200 | 1390 | 1640 | 11.6 |  |
| 2026-11-12 | 0215 | 1384 | 1634 | 14.3 |  |
| 2026-11-12 | 0230 | 1378 | 1628 | 13.6 |  |
| 2026-11-12 | 0245 | 1372 | 1622 | 16.2 | ALERT |
| 2026-11-12 | 0300 | 1366 | 1618 | 14.4 |  |
| 2026-11-12 | 0315 | 1360 | 1612 | 13.7 |  |
| 2026-11-12 | 0330 | 1354 | 1606 | 14.2 |  |
| 2026-11-12 | 0345 | 1348 | 1600 | 12.7 |  |
| 2026-11-12 | 0400 | 1342 | 1596 | 10.3 |  |
| 2026-11-12 | 0415 | 1334 | 1588 | 9.5 |  |
| 2026-11-12 | 0515 | 1310 | 1564 | 7.3 |  |
| 2026-11-12 | 0530 | 1304 | 1560 | 6.9 |  |
| 2026-11-12 | 0545 | 1298 | 1554 | 6.1 |  |
| 2026-11-12 | 0600 | 1290 | 1548 | 6.0 |  |
| 2026-11-12 | 0615 | 1282 | 1542 | 7.1 |  |
| 2026-11-12 | 0630 | 1278 | 1536 | 7.8 |  |
| 2026-11-12 | 0645 | 1270 | 1530 | 8.9 |  |
| 2026-11-12 | 0700 | 1264 | 1524 | 12.4 |  |
| 2026-11-12 | 0715 | 1258 | 1518 | 12.1 |  |
| 2026-11-12 | 0730 | 1252 | 1512 | 11.1 |  |
| 2026-11-12 | 0745 | 1246 | 1506 | 10.8 |  |
| 2026-11-12 | 0800 | 1240 | 1500 | 10.5 |  |
| 2026-11-12 | 0815 | 1232 | 1494 | 9.6 |  |
| 2026-11-12 | 0830 | 1228 | 1488 | 11.3 |  |
| 2026-11-12 | 0845 | 1222 | 1482 | 8.9 |  |
| 2026-11-12 | 0915 | 1208 | 1468 | 8.9 |  |
| 2026-11-12 | 0945 | 1196 | 1454 | 8.1 |  |
| 2026-11-12 | 1015 | 1182 | 1442 | 8.1 |  |

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
2215 1482 1718
2230 1476 1714
2245 1472 1708
2300 1466 1704
2315 1460 1700
2330 1452 1694
2345 1446 1686
0000 1440 1684
0015 1434 1676
0030 1428 1674
0045 1422 1666
0100 1416 1662
0115 1408 1656
0130 1402 1650
0145 1396 1644
0200 1390 1640
0215 1384 1634
0230 1378 1628
0245 1372 1622
0300 1366 1618
0315 1360 1612
0330 1354 1606
0345 1348 1600
0400 1342 1596
0415 1334 1588
0515 1310 1564
0530 1304 1560
0545 1298 1554
0600 1290 1548
0615 1282 1542
0630 1278 1536
0645 1270 1530
0700 1264 1524
0715 1258 1518
0730 1252 1512
0745 1246 1506
0800 1240 1500
0815 1232 1494
0830 1228 1488
0845 1222 1482
0915 1208 1468
0945 1196 1454
1015 1182 1442
```

## Movie frames

Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.

| frame | ring | tail | |
|---|---|---|---|
| 20261111T180025 | 4.8 | 5.0 |  |
| 20261111T181525 | 6.9 | 5.0 |  |
| 20261111T183025 | 7.5 | 5.0 |  |
| 20261111T184525 | 7.5 | 4.9 |  |
| 20261111T190025 | 8.9 | 5.0 | hit |
| 20261111T191525 | 9.4 | 5.0 | hit |
| 20261111T193025 | 9.5 | 4.9 | hit |
| 20261111T194525 | 9.0 | 4.9 | hit |
| 20261111T200025 | 8.8 | 5.0 | hit |
| 20261111T201525 | 9.9 | 5.1 | hit |
| 20261111T203025 | 10.7 | 5.0 | hit |
| 20261111T204525 | 11.9 | 5.0 | hit |
| 20261111T210025 | 9.5 | 5.0 |  |
| 20261111T211525 | 8.3 | 4.9 |  |
| 20261111T213025 | 5.9 | 5.1 |  |
| 20261111T214525 | 7.9 | 5.0 |  |
| 20261111T220025 | 7.3 | 5.0 |  |
| 20261111T221525 | 6.9 | 5.0 | hit |
| 20261111T223025 | 8.4 | 5.1 | hit |
| 20261111T224525 | 8.6 | 5.1 | hit |
| 20261111T230025 | 10.2 | 5.1 | hit |
| 20261111T231525 | 11.0 | 5.2 | hit |
| 20261111T233025 | 12.0 | 5.1 | hit |
| 20261111T234525 | 13.3 | 5.1 | hit |
| 20261112T000025 | 12.7 | 5.1 | hit |
| 20261112T001525 | 13.1 | 5.1 | hit |
| 20261112T003025 | 11.7 | 5.2 | hit |
| 20261112T004525 | 10.8 | 5.1 | hit |
| 20261112T010025 | 11.2 | 5.0 | hit |
| 20261112T011525 | 13.1 | 5.0 | hit |
| 20261112T013025 | 14.7 | 5.0 | hit |
| 20261112T014525 | 15.9 | 5.0 | hit |
| 20261112T020025 | 16.1 | 5.1 | hit |
| 20261112T021525 | 16.8 | 5.1 | hit |
| 20261112T023025 | 16.8 | 5.0 | hit |
| 20261112T024525 | 19.2 | 5.1 | hit |
| 20261112T030025 | 18.4 | 5.0 | hit |
| 20261112T031525 | 17.7 | 5.1 | hit |
| 20261112T033025 | 16.5 | 5.1 | hit |
| 20261112T034525 | 15.2 | 5.1 | hit |
| 20261112T040025 | 13.2 | 5.3 | hit |
| 20261112T041525 | 12.3 | 5.3 | hit |
| 20261112T043025 | 11.0 | 4.8 |  |
| 20261112T044525 | 8.7 | 4.5 |  |
| 20261112T050025 | 8.1 | 4.1 |  |
| 20261112T051525 | 7.6 | 4.1 | hit |
| 20261112T053025 | 6.7 | 4.2 | hit |
| 20261112T054525 | 5.7 | 4.1 | hit |
| 20261112T060025 | 8.4 | 4.1 | hit |
| 20261112T061525 | 8.3 | 4.1 | hit |
| 20261112T063025 | 9.8 | 4.4 | hit |
| 20261112T064525 | 11.4 | 4.9 | hit |
| 20261112T070025 | 13.5 | 5.2 | hit |
| 20261112T071525 | 13.8 | 5.1 | hit |
| 20261112T073025 | 14.7 | 5.2 | hit |
| 20261112T074525 | 15.2 | 5.2 | hit |
| 20261112T080025 | 13.4 | 5.2 | hit |
| 20261112T081525 | 12.2 | 5.2 | hit |
| 20261112T083025 | 12.5 | 5.3 | hit |
| 20261112T084525 | 10.2 | 5.1 | hit |
| 20261112T090025 | 8.7 | 5.2 |  |
| 20261112T091525 | 9.2 | 5.2 | hit |
| 20261112T093025 | 8.2 | 5.3 |  |
| 20261112T094525 | 6.9 | 5.2 | hit |
| 20261112T100025 | 6.2 | 5.3 |  |
| 20261112T101525 | 5.9 | 5.4 | hit |
| 20261112T103025 | 4.9 | 5.3 |  |
| 20261112T104525 | 6.2 | 5.2 |  |
| 20261112T110025 | 5.7 | 5.3 |  |
| 20261112T111525 | 3.9 | 5.3 |  |
| 20261112T113025 | 5.1 | 5.1 |  |
| 20261112T114525 | 3.4 | 5.3 |  |
| 20261112T120025 | 4.1 | 5.3 |  |
| 20261112T121525 | 3.8 | 5.3 |  |

*Written 2026-11-12 12:53 UTC.*
