# Learned rejecter for CCOR1 candidates

Labels from the Sungrazer reports, features from the first 5 hits, logistic regression, scored leave-one-period-out (every score below is out of sample unless marked).

**17 comets, 127 noise tracks over 4.3 weeks of frames.**

## Periods

| period | comets | noise | truth |
|---|---|---|---|
| 2025-07-17_2025-07-24 | 8 | 4 | confirmation list |
| 2025-08-18_2025-08-31 | 5 | 67 | confirmation list |
| 2026-10-17_2026-10-24 | 4 | 56 | hunter claims (provisional) |

## Feature sets tried

Noise tracks that pass when the threshold is set to keep every comet, out of sample. The report below uses the best: **start radius** (L2 1).

| feature set | L2 | noise kept at full recall |
|---|---|---|
| start radius (r0) | 1 | 48/127 |
| start radius (r0) | 10 | 50/127 |
| + speed for radius (r0, log_v_over_r) | 1 | 76/127 |
| + speed for radius (r0, log_v_over_r) | 10 | 71/127 |
| + brightening (r0, log_v_over_r, sig_slope) | 1 | 81/127 |
| + brightening (r0, log_v_over_r, sig_slope) | 10 | 72/127 |
| + brightness (r0, log_v_over_r, sig_slope, sig_mean) | 1 | 76/127 |
| + brightness (r0, log_v_over_r, sig_slope, sig_mean) | 10 | 71/127 |
| + straightness (r0, log_v_over_r, sig_slope, sig_mean, rms) | 1 | 95/127 |
| + straightness (r0, log_v_over_r, sig_slope, sig_mean, rms) | 10 | 76/127 |
| + heading (r0, log_v_over_r, sig_slope, sig_mean, rms, heading) | 1 | 92/127 |
| + heading (r0, log_v_over_r, sig_slope, sig_mean, rms, heading) | 10 | 81/127 |
| everything (r0, log_v_over_r, speed, heading, pa, sig_mean, sig_max, sig_slope, elong, edge, rms, consecutive) | 1 | 66/127 |
| everything (r0, log_v_over_r, speed, heading, pa, sig_mean, sig_max, sig_slope, elong, edge, rms, consecutive) | 10 | 71/127 |

## What a threshold buys

Candidates with at least 5 hits that the rules keep. 'Baseline' rows are the hand rules for comparison.

| rule | comets kept | noise kept | noise per week |
|---|---|---|---|
| baseline: every candidate at 5 hits | 17/17 | 127/127 | 29.6 |
| baseline: watch tier (5 hits, 10 sigma) | 11/17 | 1/127 | 0.2 |
| baseline: steady (5 hits, started >= 550 px out) | 17/17 | 50/127 | 11.7 |
| model, every comet kept (score >= 0.284) | 17/17 | 48/127 | 11.2 |
| model, 90% of comets kept (score >= 0.334) | 16/17 | 41/127 | 9.6 |
| model >= 0.5 | 13/17 | 24/127 | 5.6 |

## What the model keys on

Weights on standardised features (one standard deviation of the feature moves the log-odds by this much). Positive favours comet.

| feature | weight |
|---|---|
| r0 | +1.46 |

## Every comet, out of sample

| period | track | hits | best sigma | started (px) | score |
|---|---|---|---|---|---|
| 2026-10-17_2026-10-24 | 20261022T1300_1510_1200 | 7 | 8.3 | 560 | 0.284 |
| 2025-08-18_2025-08-31 | 20250820T0745_1610_1124 | 11 | 9.4 | 592 | 0.334 |
| 2025-07-17_2025-07-24 | 20250724T0900_1612_1244 | 25 | 16.1 | 637 | 0.419 |
| 2025-08-18_2025-08-31 | 20250825T1930_1696_1106 | 7 | 9.2 | 671 | 0.451 |
| 2025-07-17_2025-07-24 | 20250719T0515_1574_1410 | 6 | 9.7 | 697 | 0.513 |
| 2025-07-17_2025-07-24 | 20250723T1045_1754_1252 | 40 | 25.8 | 770 | 0.626 |
| 2025-07-17_2025-07-24 | 20250717T0745_1720_1398 | 52 | 31.5 | 807 | 0.679 |
| 2026-10-17_2026-10-24 | 20261021T0900_1588_1458 | 41 | 15.3 | 769 | 0.681 |
| 2025-08-18_2025-08-31 | 20250829T2200_1778_1336 | 23 | 10.8 | 827 | 0.685 |
| 2025-07-17_2025-07-24 | 20250720T0330_1668_1506 | 46 | 22.5 | 831 | 0.711 |
| 2025-07-17_2025-07-24 | 20250717T0715_1652_1534 | 49 | 38.1 | 838 | 0.719 |
| 2025-07-17_2025-07-24 | 20250721T1230_1798_1394 | 61 | 39.2 | 872 | 0.761 |
| 2025-08-18_2025-08-31 | 20250824T0030_2014_1144 | 20 | 38.1 | 990 | 0.858 |
| 2025-08-18_2025-08-31 | 20250818T2015_2004_1200 | 35 | 33.4 | 992 | 0.860 |
| 2025-07-17_2025-07-24 | 20250724T1345_1956_1528 | 39 | 251.4 | 1076 | 0.921 |
| 2026-10-17_2026-10-24 | 20261023T0830_2004_1370 | 84 | 264.6 | 1081 | 0.964 |
| 2026-10-17_2026-10-24 | 20261019T1415_2036_1354 | 69 | 264.6 | 1105 | 0.970 |

## Noise the model likes most

| period | track | hits | best sigma | started (px) | score |
|---|---|---|---|---|---|
| 2026-10-17_2026-10-24 | 20261019T2245_1766_1644 | 7 | 7.8 | 1025 | 0.945 |
| 2026-10-17_2026-10-24 | 20261022T2145_1924_1330 | 5 | 8.5 | 992 | 0.928 |
| 2026-10-17_2026-10-24 | 20261017T0045_1846_1448 | 7 | 8.6 | 974 | 0.918 |
| 2025-08-18_2025-08-31 | 20250819T1345_2006_1430 | 7 | 9.6 | 1073 | 0.910 |
| 2026-10-17_2026-10-24 | 20261024T2000_1958_936 | 6 | 7.4 | 953 | 0.905 |
| 2025-08-18_2025-08-31 | 20250823T1645_1932_1524 | 5 | 7.9 | 1054 | 0.900 |
| 2026-10-17_2026-10-24 | 20261023T0100_1728_1544 | 5 | 8.2 | 931 | 0.888 |
| 2026-10-17_2026-10-24 | 20261024T2245_1850_1342 | 5 | 7.8 | 929 | 0.886 |
| 2026-10-17_2026-10-24 | 20261022T1700_1802_1398 | 6 | 7.6 | 911 | 0.871 |
| 2026-10-17_2026-10-24 | 20261019T0015_1892_1146 | 5 | 7.4 | 907 | 0.867 |
| 2026-10-17_2026-10-24 | 20261018T1500_1680_1554 | 6 | 9.1 | 901 | 0.862 |
| 2025-08-18_2025-08-31 | 20250827T1600_1910_1442 | 5 | 7.7 | 993 | 0.860 |
| 2025-08-18_2025-08-31 | 20250826T0030_1990_1202 | 5 | 7.6 | 979 | 0.849 |
| 2025-08-18_2025-08-31 | 20250830T2030_1950_1190 | 5 | 8.3 | 937 | 0.813 |
| 2025-08-18_2025-08-31 | 20250829T1215_1740_1576 | 5 | 7.3 | 931 | 0.807 |
| 2025-07-17_2025-07-24 | 20250718T1730_1926_1146 | 5 | 8.0 | 904 | 0.797 |
| 2026-10-17_2026-10-24 | 20261022T1230_1790_1104 | 5 | 7.4 | 798 | 0.731 |
| 2026-10-17_2026-10-24 | 20261021T0900_1760_1076 | 5 | 8.5 | 764 | 0.673 |
| 2025-08-18_2025-08-31 | 20250822T1400_1656_1482 | 5 | 8.0 | 806 | 0.656 |
| 2025-08-18_2025-08-31 | 20250829T1945_1812_864 | 5 | 7.8 | 777 | 0.615 |

Dropped as ambiguous: tracks matching a rejected claim, duplicates of a comet track, and bright (>= 20 sigma) unreported tracks overlapping a LASCO claim in time.

*Written 2026-10-30 01:49 UTC; model in model.json.*
