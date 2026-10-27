# kreutz — Kreutz sungrazer detector for CCOR coronagraphs

Goal: spot Kreutz-family sungrazing comets in NOAA's CCOR-1 (on GOES-19) and
CCOR-2 (on SWFO-L1) coronagraph images early enough to report them to the
[NRL Sungrazer Project](https://sungrazer.nrl.navy.mil/).

A coronagraph blocks the Sun with a disc (the *occulter*, held by a support arm)
so the faint corona and anything near the Sun becomes visible. Kreutz comets show
up as faint dots that move in a nearly straight line toward the Sun across many
15-minute frames, getting brighter. Stars drift slowly the other way, all together.

Status: **step 2 of 5 — detector on archive data.** We can download frames,
inspect them, keep a local "answer key" of what human hunters reported and what
got confirmed, run a detector over a date range, and grade it against the key.

## Setup

```bash
uv venv --python 3.12 .venv            # or any Python >= 3.12
uv pip install --python .venv/bin/python -e '.[dev]'
source .venv/bin/activate
pytest
```

## Commands

```bash
# Archive frames for a date range (inclusive). Idempotent and resumable.
python -m kreutz fetch --camera ccor1 --start 2025-07-17 --end 2025-07-24

# Whatever is new on the real-time feed (last 6 h by default; at most one poll per 15 min).
python -m kreutz fetch --camera ccor1 --live [--hours 6] [--force]

# Describe one frame and write a viewable PNG next to it (or --png path).
python -m kreutz inspect data/frames/ccor1/20250717/CCOR1_1B_20250717T120025_V00_NC.fits

# Scrape Sungrazer reports + confirmation lists into data/reports.sqlite.
python -m kreutz reports sync [--camera ccor1 ccor2] [--since 2025-01-01] [--force]

# Reports whose first frame is in a window.
python -m kreutz reports list --camera ccor1 --start 2025-07-17 --end 2025-07-24

# Run the detector (cached per frame/window; rerunning is cheap).
python -m kreutz detect --camera ccor1 --start 2025-07-17 --end 2025-07-24 \
    [--dump CCOR1_1B_20250717T074525_V00_NC.fits]   # write every stage of that frame

# Grade it against the answer key; writes contact sheets for every unmatched track.
python -m kreutz score --camera ccor1 --start 2025-07-17 --end 2025-07-24

# Live: poll the feed every 15 min, track the last 48 h, write data/live/<camera>/state.json (+ images).
python -m kreutz watch --camera ccor1 [--once]

# Dashboard for whatever the watcher wrote, on http://<host>:8080/
python -m kreutz serve [--port 8080]
```

## Live watcher and dashboard

`kreutz watch` is the detector on the real-time feed. Each cycle fetches new
frames and reports, runs stage A on the new frame in-process (a resident
`frames.StageA` keeps the 16 background and 8 star frames, so a frame costs
one median and one star model), stacks its window, feeds it to a tracker that
persists between cycles (`tracker.pkl`, rebuilt from the cached peaks when a
rule, stack or grid version changes), matches tracks against hunter reports
and writes `data/live/<camera>/state.json`, a contact sheet and thumbnail per
followed track, a downscaled preview of the latest frame, and
`activity.json` (what it is doing now, and the last 300 events). Track ids
are `<first frame>_<x>_<y>` so they are stable across cycles. Polls happen
at the minute within each 15-minute slot that minimises the wait after SWPC
posts a frame, chosen from the publication log (Oct 2026: 2 min past the
slot, expected wait 5.1 min); the 15-minute guard still rules.

Reports: CCOR reports are matched by position; LASCO C2/C3 claims are synced
60 days deep and matched by time only (a claim whose frames fall within 6 h of
a track is shown as "reported on C3 (time only)"), so UNREPORTED means
unreported on any Sungrazer camera. The listing is walked in full once a day.

`kreutz serve` is a standard-library HTTP server for the single-page
dashboard in `src/kreutz/web/`: a status line per camera, the current stage
with progress and the last five events (full log expandable), the tracks
being followed (with any matching report and the lead time including our
latency), a collapsed list of recently rejected tracks, and a detail view that
animates the track's frames. The page polls `/api/state` every minute and
`/api/activity` every 3 s.

Alert tiers (`live.TIERS`, chosen from real-time operating-point tables in
`results/STATUS.md`): **alert** = 4 hits and best significance ≥ 15σ
(Oct 17–24 2026: precision 0.60, 3 of 5 comets, median real lead −3.6 h);
**watch** = 5 hits and ≥ 10σ. The dashboard sorts alerts first; the activity
feed logs ALERT events. Discord: put a webhook URL in
`data/state/discord_webhook.txt` (or `$KREUTZ_DISCORD_WEBHOOK`) and every new
alert-tier object is posted once, with its contact sheet; set
`$KREUTZ_DASHBOARD_URL` to append a link. Without a URL nothing is posted.

Labels: each card has comet / noise / unsure buttons. A click is appended to
`data/live/<camera>/labels/labels.jsonl` with a frozen copy of the track and
its stacked cutout (`<id>_stack.png`), for training a rejecter later. Labels
change only display order and alerts; the tracker keeps following everything
by its own rules, and a noise label on an object that later reaches the alert
tier or matches a hunter's report is marked *contradicted* and announced.

Deployment: `deploy/` has systemd units (`kreutz-watch@ccor1`,
`kreutz-serve`) that run as the repo owner with `Restart=always`. To deploy a
change without sudo: `git pull`, then `kill $(systemctl show -p MainPID
--value kreutz-watch@ccor1)` while the watcher is idle; it comes back in 60 s
and waits for the next poll slot. Logs: `journalctl -u kreutz-watch@ccor1`.

## Results (`results/`, committed)

Every `kreutz score` run writes `results/<camera>/<start>_<end>/`:
`scorecard.txt` (all printed output, headed by the code commit and stage
versions), `tracks.json` (tracks that survived the rejection rules) and
`sheets/` (contact sheets of every true positive and every unmatched track that
reached 5 hits; the rest stay in `data/derived/`). Each run replaces the
previous one; compare runs with `git log -p results/`.

[`results/STATUS.md`](results/STATUS.md) says what the latest numbers mean and
lists the next steps.

## Data sources (verified 2026-10-24)

| what | where | notes |
|---|---|---|
| CCOR-1 archive | NCEI S3 bucket behind `https://archive.data.noaa.gov/satellite-spaceweather`, keys `SWFO/GOES-19/CCOR-1/ccor1-l1b/YYYY/MM/DD/*.fits` | One FITS per frame (not tars). From Feb 2025. ~9 MB each, 96/day. Listed with the S3 API (`?list-type=2&prefix=...`). |
| CCOR-1 real-time | `https://services.swpc.noaa.gov/products/ccor1/fits.json` → `.../ccor1/fits/` | ~30 days rolling. `fits.json` is sometimes briefly served as `[]` while being rewritten; we fall back to the directory listing. The old `/experimental/products/swfol1/ccor-1/fits/` path is empty. |
| CCOR-2 archive | `SWFO/SOLAR-1/CCOR-2/ccor2-l1a/...` in the same bucket | **No L1B exists** for CCOR-2 (archive or live), so we use L1A. Its headers still say `BUNIT = MSB`. 2048×1920, 24.3″/px. |
| CCOR-2 real-time | `https://services.swpc.noaa.gov/products/ccor2/fits.json` | L1A files. |
| Reports | `https://sungrazer.nrl.navy.mil/plain-text-reports?field_camera_value=CCOR1&items_per_page=100&page=N` and `/report/reportXXYYYYMMDDHHMMSS` | |
| Confirmations | `/confs-*` posts linked from `/site_updates` | Fixed-width tables headed `CCOR#` (Mar 2025), `CCR1#`, `Soho#`. |

Data level: CCOR-1 uses **L1B** (calibrated, brightness in units of mean solar
brightness, "MSB"), falling back to L1A only if a day has no L1B.

Politeness: every request has a descriptive User-Agent; consecutive requests to a
host are at least 0.5 s apart; anything that *polls* (live feed, report sync) is
limited to once per 15 minutes per endpoint (state in `data/state/polls.json`).
Nothing already on disk is downloaded again.

## Data layout

```
data/                         (gitignored, all rebuildable)
  frames/<camera>/<YYYYMMDD>/CCOR1_1B_20250717T120025_V00_NC.fits
  reports.sqlite              answer key (below)
  cache/sungrazer/reports/    each report page's HTML, fetched once
  state/polls.json            last poll time per endpoint
```

Archive and live feeds use identical filenames, so a frame is stored once
whichever source it came from. Downloads go to `*.part` and are renamed when
complete, so an interrupted fetch just resumes.

## FITS frames

CCOR-1 L1B: HDU 0 is an empty header; HDU 1 is a tile-compressed float32 image,
2048 wide × 1920 tall. ~11% of pixels are NaN (outside the circular field and
inside the occulter). Plate scale 19.33″/px; the Sun's disc is ~49 px in radius
and sits near report coordinates (1041, 962).

**Frame time** = `DATE-OBS` (exposure start), which matches the filename and the
`HHMM` hunters quote: "1215" means the frame starting 12:15:25. `DATE-AVG`
(mid-exposure, ~14 s later) is kept for precise motion fits. All UTC.

**Quality** (`kreutz.fitsio.frame_quality`): the header carries telemetry checks.
`*_Q` flags are True when things are *good*. A frame is unusable when any of
these is off: `ISNORMAL`, `DCMPRS_Q`, `IMGBLK_Q`, `EARFOV_Q` (Earth in view),
`GTTRAC_Q`; or when `E_ECLPSE`/`M_ECLPSE` (Earth/Moon eclipse state),
`BADBLK_N`/`MISBLK_N` are non-zero; door not `OPEN`; `MAJSTORM` (particle
storm); `OFFSUNDR` > 100″ (spacecraft off-pointed); or the image is mostly empty.

Two more exclusions live in stage A because they need neighbouring frames:

- **Calibration off-points.** In Oct 2026 the spacecraft off-points every day
  07:00–07:45 UTC: `CRVAL1/2` jump by ~1°, but `OFFSUNDR` reads only ~10″ on
  some of those frames, and no `_Q` flag is set. Any frame whose `CRVAL` moved
  > 60″ from the last accepted frame is dropped (4 frames in a row agreeing on a
  new pointing are accepted as a lasting re-point). July 2025 had 4 such frames
  in the week; Oct 2026 had 29.
- **Earth-guard.** The frame on each side of an Earth-in-view (`EARFOV_Q=F`,
  daily 04:15–05:15 UTC in Oct 2026) or eclipse frame is dropped too: it carries
  unflagged scattered Earthlight, and its stars failed to register. Eclipse
  flags (`E_ECLPSE`, `M_ECLPSE`) were 0 all week; GEO eclipse season had ended.

Two flags are only *warnings*, after looking at the frames they mark:
`MONFOV_Q=F` (the Moon is in view, a small disc the detector masks; new Moon
2025-07-24 put it in the field for 18 h, during which hunters found CCR1 0079) and
`BORSGT_Q=F` (false every day ~03:45–06:30 UTC as the Sun passes close behind
the Earth seen from GEO; images are normal). Treating them as fatal cost two of
the eight benchmark comets.

## Coordinate convention (fixed; tested in `tests/test_coords.py`)

Everything in this project (stored, compared, submitted) uses **report
coordinates**:

* `(x, y)`, origin **(0, 0) at the upper-left** of the image as displayed with
  solar north up (Sungrazer's "Upper Left" option, and how SWPC images look);
* x grows to the right, **y grows downward**;
* 0-based; integer values are pixel centres.

FITS stores the bottom row first, so with the array as astropy loads it
(`data[row, col]`):

```
x = col
y = (1920 - 1) - row          # kreutz.coords.fits_to_report / report_to_fits
```

FITS 1-based numbers (`CRPIX`, ds9) are `col = X_fits - 1`, `row = Y_fits - 1`.
Hunters who pick another corner ("Lower Left", "Upper/Lower Right") are mirrored
into this convention on import (`frames.x_ul, y_ul`); a missing origin is taken as
Upper Left.
PNG previews from `inspect` are drawn in this orientation, so a pixel in the PNG
is the same `(x, y)`.

How it was checked: `scripts/verify_coords.py` stacks background-subtracted
cutouts at the positions hunters reported for the confirmed comets of
2025-07-17..24. With this convention the stack peaks at exactly the centre at
~14× the background scatter; with no flip, or with mirrored x, there is no peak.
CCOR-2 is assumed to follow the same convention (same pipeline, same image
size, `L2RFLIP`/`RECTIFY` set identically); it has not yet been checked against
confirmed CCOR-2 comets. Hunter positions are good to about a pixel, so 0- vs 1-based can't be told
apart from this; we chose 0-based.

### Reports not measured on the full-resolution frame

The form's "image size" field can't be trusted on its own. 11 confirmed CCOR-1
reports (Jiangao Ruan, Zhijian Xu, Robert Pickard; Feb–Aug 2025) declare
1024×1024 but quote positions up to x = 2032, y = 1745: they were measured on the
2048×1920 frame and are used as they are. `scripts/verify_report_scale.py`
confirms it on their frames: at scale 1 the median of 51×51 boxes peaks 1 px
from the centre at 6× the scatter, and at scale 2 every position falls off a
1024-wide image.

Bill Collins's reports are the other case. They declare 1024×1024 with a "Lower
Right" origin, but the numbers grow right and down, as from an upper-left
origin. His view is turned to solar north up (by −CROTA, 25.5° in Oct 2026) and
reduced about 2.2×, and the Sun sits at (610, 618) in his image. His two
claims of Oct 19 and Oct 23 2026 fit two bright tracks to 3.5 px rms with a
fitted similarity transform: scale 2.208, rotation 25.0°, offset. Fitting either
comet alone predicts the other to 5–14 px. Neither the rotation (it follows the
solar P angle) nor his crop is known for other dates.

So the mapping is decided per report by `coords.report_mapping` and stored in
`reports.mapping`:

| mapping | when | positions |
|---|---|---|
| `full` | declared 2048×1920, no size given, or any position > 1023 | this convention (origin mirrored using 2048×1920) |
| `transform` | reporter and date covered by `coords.REPORT_TRANSFORMS` (Bill Collins, 2026-10-17..24) | fitted transform of the numbers as typed |
| `unmapped` | a smaller declared size with every position inside it, or a reporter with a transform outside its verified dates | dropped (`x_ul = NULL`) |

Unmapped reports have no positions, so they're out of truth, velocity grids
and envelopes. The scorecard lists the ones in its window. 45 reports are
unmapped, none of them confirmed.

### Orientation guard

The pipeline records how it flipped/rotated each image (`L2RFLIP`, `NROT90`) and
the spacecraft's yaw state (`YAWFLIP`). The convention above has been verified
only for the combinations in `coords.VERIFIED_ORIENTATIONS`:

| camera | L2RFLIP, NROT90, YAWFLIP | seen | verified with |
|---|---|---|---|
| ccor1 | T, 0, 2 | Jul 2025 | 50 positions of 8 confirmed comets, peak at (0, 0) |
| ccor1 | T, 2, 0 | Oct 2026 (after a yaw flip) | 30 positions of unreviewed hunter claims, peak at (0, -1) |

After the yaw flip the pipeline rotates the image 180° so solar north stays up;
the occulter arm, fixed to the instrument, moves from the top to the bottom of
the picture. Any other combination makes `read_frame` raise
`OrientationChanged`; verify it with `scripts/verify_coords.py
--allow-unverified` before adding it. The fetcher prints each day's flags.
CCOR-2 has no verified combination yet, so the detector refuses its frames.

## Answer key: `data/reports.sqlite`

* `reports`: `report_id`, `reporter`, `report_timestamp` (posting time, UTC),
  `image_date`, `camera` (`ccor1`/`ccor2`), `sighting_type`, `comet_group`,
  `origin`, `image_size`, `time_referenced`/`parent_id` (for follow-ups),
  `endorsements`, `status`, `frames_text` (raw position block as posted).
* `frames`: `report_id, seq, frame_time (UTC), x, y` as typed, `x_ul, y_ul` in
  our convention, and the `raw` line. Unparseable lines are kept with NULLs, and
  so are positions whose report is `unmapped` (`reports.mapping`, see above);
  every sync rebuilds them from the raw text.
* `confirmations`: rows of the admins' confirmation tables, linked to the
  matching `report_id`.

`status` is re-derived on every sync:
`confirmed` (in a confirmation table), `retracted` (the hunter posted a
"Retracting Report" for it), `rejected` (a confirmation list covering that date
exists for the camera and it's not on it), `known` ("Known Comet Now Entering"),
otherwise `reported`. Follow-ups inherit their parent's status unless they were
confirmed themselves.

Quirks we handle (worth knowing when reading reports):

* **Report ids are US Eastern local time, not UT** (despite the form
  instructions): `report_jr_20261024054650` lists frames up to 09:15 UT, so it
  was posted at 05:46 EDT = 09:46 UT. Confirmation-list times use the same
  clock. We store true UTC.
* Frame lines are just `HHMM x y`; the report's date is the date of the *first*
  frame, and we roll to the next day when the clock goes backwards.
* A confirmation can belong to a follow-up rather than the original claim (e.g.
  CCR1 0073, 2025-07-17: the first report was noise, the follow-up was the comet).
* Hunters make typos (wrong date, a dropped digit); raw text is always kept.

Benchmark week 2025-07-17..24 (CCOR-1): 8 confirmed comets, CCR1 0073–0080,
all linked to their reports.

## Detector (`src/kreutz/detect/`)

Three stages, each cached under `data/derived/<camera>/<version>/` and each
inspectable (`--dump` writes every intermediate array of a frame as .npy + .png):

**A. Per frame** (`detect/frames.py`). Exact zeros are pipeline fill and become
NaN like the occulter and field edge. *Background* = median of the previous 16
good frames (~4 h); anything static that long is corona, glow or occulter.
*Stars*: the ~300 brightest point sources of this frame and of each of the 8
before it are matched (a vote over pairwise offsets, then a rotation +
translation fit); the earlier frames are warped onto this one and their median is
a stars-only model, which is subtracted. Pixels within 4 px of stars in the
model are masked. The measured drift is ~(+2.0, −0.3) px/frame in July 2025:
stars move right (west), as they should. *DoG* (σ 1.2 / 4.0 px) keeps point
sources; dividing by the local noise gives significance; the map is saved at
half resolution.

A frame is **warm** when ≥ 6 of the 8 star-model frames registered and its 16
background frames span ≤ 6 h. After a gap (Earth in view, an off-point) the
background straddles the gap until enough new frames arrive, and windows built
on such frames made fake slow movers: in Oct 2026, 8 of the 10 strongest false
positives appeared at 10:15–10:30 each day, moving at about (−1.4, 0) px/frame,
just before the last pre-gap frames left the background. Stage C starts **no new
tracks** from a window that contains a cold frame; tracks already being
followed continue through it. Cost: in Oct 2026, windows ending ~08:00–12:30
can't start a track.

**B. Per window of 8 frames** (`detect/stack.py`). For every trial velocity the
8 maps are shifted back along that path and summed, so an object moving at that
velocity piles up at its newest position. The brightest of the 8 values at each
pixel is dropped and the rest divided by √7: a cosmic ray hits one frame, so it
adds nothing, while a real mover loses 1/8 of its signal. (Earlier versions
clipped each map at +8σ, which capped every stack at 8·√8 = 22.6σ: all bright
objects tied there and significance couldn't rank them.) Maps are floored at
−3σ so a masked-star hole can't cancel a real object. Shifts use real frame
times, so gaps are fine. Peaks above 5σ are kept.

The velocity grid is per calendar month, from the **Kreutz envelope**
(`detect/envelope.py`): the lines fitted to that month's confirmed CCOR-1
Kreutz reports, as a velocity box plus 2 px/frame margin, every 0.5 px/frame
(July: 456 velocities). The same envelope gives stage C the allowed paths; see
`off-envelope` below.

**C. Tracking** (`detect/track.py`). Windows slide one frame at a time. A peak
that turns up where the previous window predicted (within 6 px and 2 px/frame)
in 3 consecutive windows becomes a candidate, then is followed through up to 2
missed windows. The times it reached 3, 4 and 5 hits are recorded separately;
which of those is "good enough to submit" is decided from the scorecard, not
in code.

Frames that fail the quality check, and the binned `*_0C.fits` campaign frames
(e.g. 2025-07-18 03:45–06:20), are left out entirely. The Moon, when in view, is
masked: any connected region > 1.3× background and > 2,000 px, plus 25 px.

One object gives several nearby stack peaks (neighbouring velocities), so a
peak within 12 px of one already claimed in the same window doesn't start a new
track. Candidate tracks are merged into the longest when they share ≥ 2 hit
times within 8 px, or when they move at the same velocity (within 2 px/frame)
and share ≥ 2 hit times within 40 px along the motion and 8 px across it. A
bright comet's head and tail both make peaks, about 26 px apart along the path:
in Oct 2026 one comet came out as three tracks. The merged track counts from
the earliest moment any of its pieces was submittable.

Rejection rules are re-checked after every hit, and some use all hits so far,
so a comet that was clean at 3 hits can be rejected later (glare-zone, as it
nears the occulter). Each track therefore records its **status when it reached
3, 4 and 5 hits** (`status_at`) as well as its final status. Scoring uses the
status at the threshold; a later rejection is printed but never undoes a
submission.

### Rejection rules and why each exists

A candidate track is rejected (kept in the output, marked `rejected:<rule>`) if:

| rule | test | why |
|---|---|---|
| `stationary` | speed < 1 px/frame | Things that don't move relative to the Sun are residual background (glare, hot pixels, slowly changing corona). Slowest confirmed comet: 3 px/frame. |
| `star-drift` | velocity within 1.5 px/frame of the measured star drift | Imperfectly subtracted stars move together at the drift velocity. |
| `outbound` | moving away from the Sun (cos of angle to the Sun direction < 0) | Kreutz comets are seen falling inward. Cost: 6 of 106 confirmed CCOR-1 reports moved away from the Sun: 5 Kracht II / non-group, 1 Kreutz moving nearly sideways. |
| `off-envelope` | direction of motion, or signed miss distance of the path from the Sun, outside this month's Kreutz envelope (+15°, +80 px margins) | All Kreutz comets share one orbit, so in a given month they arrive from one side on nearly parallel paths; the side rotates through the year (Mar: from the left moving right; Jul: lower right moving up-left; Oct: right moving left). Things entering from anywhere else are not Kreutz comets: in Oct 2026 that included tracks entering at the left edge moving right. Built from confirmed reports (±1 month) where they exist; NRL has posted no confirmations since Aug 2025, so Oct–Jan use that month's pending hunter claims trimmed to the 10–90th percentiles, marked PROVISIONAL in the output. Cost: comets of other groups (Kracht, Meyer, non-group: 10 of 116 confirmed CCOR-1 reports). |
| `glare-zone` | median distance from the Sun < 320 px | Variable glare at the occulter rim dominated the false positives (250 of 452 surviving tracks on 2025-07-17). Confirmed comets were never reported inside 392 px, and we want them earlier than that anyway. |
| `edge` | never more than 25 px from the occulter, arm, field edge or array edge | The background and noise estimates break down at the borders of valid data. Cost: a comet entering the field is flagged ~3 frames later. |
| `extended` | median axis ratio > 3 of the blob under the hits, measured on the **stack at the track's velocity** in a 50-px box, with the blob cut at a quarter of the peak (≥ 2.5σ) | Streamer edges and other linear structures that survive the DoG filter can slide along at comet-like speeds. In a single frame they look no different from a comet (both measured 1–3 on the test weeks). In the stack at the trial velocity a comet piles up into a compact head while a sliding ridge stays long: on both test weeks all 13 true comets measure 1.0–2.2 and the recurring Oct 2026 streamer tracks 2.6–10.8. Cutting at a quarter of the peak leaves a bright comet's tail below the cut. The single-frame version rejected the brightest comet of Oct 19 2026 (265σ, with a tail) as extended. Cost: a faint comet with a tail as bright as its head. |

## Scoring (`kreutz score`)

Truth objects, matching and lead times are described in `src/kreutz/score.py`.
Two choices matter for reading a scorecard:

* **Status at k.** A track counts at threshold k only if it was submittable when
  it got its k-th hit (`status_at[k] == "candidate"`), not by its final status.
  The scorecard lists every track that matches a truth object with its status at
  3/4/5 hits and at the end, so a rule that fires late is visible.
* **Real lead.** The frame time of the k-th hit isn't when we could submit. SWPC
  publishes a frame a median of **14.6 min** after exposure start (90th
  percentile 22.6 min; 2,358 files, Oct 1–25 2026, from the live directory
  listing's timestamps). We poll at most every 15 min, so add 7.5 min on
  average, plus about 30 s to process the new frame. The "real" column adds
  that total, about 22.6 min. `kreutz fetch --live` logs each file's listing time and
  our first sight of it in `data/state/published_<camera>.json`, and the
  scorecard uses whatever that log holds at run time.

