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

Reports: this camera's CCOR reports are matched by position (12 px). Reports
on the other CCOR camera are synced too and matched in solar coordinates
(`solar.py`): each reported pixel goes through that camera's WCS to
helioprojective (Tx, Ty) in solar radii as seen by that observer, then through
our camera's WCS into our pixels at the same time, and matches within 40 px
(`CROSS_MATCH_PX`; the 2026-11-02 comet, reported on CCOR-2 by Zesheng Yang
8 min before our first CCOR-1 hit, converts to 10 px of our track). Working in
solar radii absorbs most of the GOES-19/L1 parallax for an object near the Sun.
The WCS comes from the frame nearest the time on disk; for CCOR-2 the nearest
frame in SWPC's live listing is fetched once and kept. Such a match shows as
"reported by X on CCOR-2". LASCO C2/C3 claims are synced 60 days deep and
matched by time only (a claim whose frames fall within 6 h of a track is shown
as "reported on C3 (time only)"), so UNREPORTED means unreported on any
Sungrazer camera. The listing is walked in full once a day.

`kreutz serve` is a standard-library HTTP server for the single-page
dashboard in `src/kreutz/web/`: a status line per camera, the current stage
with progress and the last five events (full log expandable), the tracks
being followed (with any matching report and the lead time, including our
latency, measured from our **alert**: a report posted before the alert is a
loss however long the track was being followed; the detail view shows both
escalation times, following and alert), a collapsed list of recently rejected
tracks, and a detail view that animates the track's frames. A track seen from
both CCOR cameras carries a "same object on CCOR-2/CCOR-1" link (its last
hits converted through both WCS land on the other camera's track). The page polls `/api/state` every minute and
`/api/activity` every 3 s.

Alert tiers (`live.TIERS`, chosen from real-time operating-point tables in
`results/STATUS.md`): **alert** = 4 hits and best significance ≥ 15σ
(Oct 17–24 2026: precision 0.60, 3 of 5 comets, median real lead −3.6 h);
**watch** = 5 hits and ≥ 10σ. The dashboard sorts alerts first; the activity
feed logs ALERT events. Alerts off the machine (`notify.py`): every object
that first reaches the alert tier is sent once per channel, with its contact
sheet, to whichever of these is configured by a one-line file in
`data/state/` or an environment variable:

- **ntfy push** to a phone: `ntfy_url.txt` (`$KREUTZ_NTFY_URL`) holds the
  topic URL, `https://ntfy.sh/<topic>`; subscribe to that topic in the ntfy
  app. Priority 5 for an unreported object (the app can sound through
  do-not-disturb), 4 for one a hunter has already reported. The topic name is
  the only secret, so make it long and random.
- **Discord**: `discord_webhook.txt` (`$KREUTZ_DISCORD_WEBHOOK`) holds a
  webhook URL. Like the phone push it hears only alerts: a track starting
  to be followed sends nothing (`notify.CHANNEL_EVENTS`).

An alert reads: reported or not; hits, brightest sigma and whether it is
brightening; distance from the Sun, speed and heading; the owner's tag if
any; and the newest five positions in the report form's layout. Its picture
is the contact sheet as an animation (`export.write_sheet_gif`): one frame per
column, the three pictures in a row (raw frame, that frame's map, the last 8
maps stacked), the time stamped on, resting on the newest; Discord plays it
in place. The owner did not find the still sheet useful (2026-11-09).

After an alert the watcher also builds and publishes the hunter's package
(`Watcher._packages`, once the cycle's state is out) and sends its README
link as a quiet follow-up: that link opens anywhere, the dashboard's only on
the home network. The package is published once more when the track has
ended (no hit for 2.5 h), so the link ends up showing the whole pass.
`auto_export.txt` holding `off` stops both. Builds are serialised across the
watcher and the dashboard server by a file lock (`movie.build_lock`).

Nothing links the dashboard: it sits on a network the owner's phone cannot
reach. Tapping the phone push opens the camera's live-finds folder on GitHub
(`export.finds_url`), the follow-up the package; the package link is in angle
brackets so Discord shows no embed under it.
`python -c "from kreutz import notify; print(notify.send_test())"` sends a
test on every configured channel. With nothing configured nothing is sent.

Labels: each card has comet / noise / unsure buttons. A click is appended to
`data/live/<camera>/labels/labels.jsonl` with a frozen copy of the track and
its stacked cutout (`<id>_stack.png`), for training a rejecter later. Labels
change only display order and alerts; the tracker keeps following everything
by its own rules, and a noise label on an object that later reaches the alert
tier or matches a hunter's report is marked *contradicted* and announced.
Steady tier: below alert (4 hits, 15 sigma) and watch (5 hits, 10 sigma) sits
*steady*: `STEADY_HITS` (5) hits in consecutive frames at any brightness, first
hit at least `STEADY_R` (550) px from the Sun. Faint comets peaking at 8-9
sigma never reach the brightness tiers and show themselves only by
persisting; every real comet so far started 560+ px out, while long-lived
noise lives in the streamer belt at 380-420 px (`results/ccor1/learn/`). On
the benchmark weeks it keeps every comet at about 12 noise tracks a week. It
is shown in the top list and the change log, never posted to Discord.

Sparks: a section under FOLLOWING lists two-hit chains that are bright on
both hits (`SPARK_SIGMA`, 8 sigma: about two a day, and half the benchmark
comets qualify at two hits) and pass the same rejection rules as candidates
(outbound, off-envelope, glare, edge, extended, stationary, star-drift), with
the detector's stacked cutout. Open chains die on their first miss, so sparks
are always as of the newest frame: a third hit moves one up to FOLLOWING
under the same id, a miss drops it. Because a spark lives one frame, the
state also carries `spark_log`, the last `SPARK_LOG_H` (6) hours of sparks
with their fate (grew into a track, rejected at the third hit, gone), listed
under the section. The change log notes each new spark once. Sparks on top
of a followed object are not listed.

Learning from the reports: `kreutz learn` builds a labelled set of candidates
without anyone in the loop. Every track with 5+ hits that the rules kept is a
*comet* if it matches a non-rejected Sungrazer report (the scorer's truth
matching) and *noise* if the reports stayed silent; tracks matching a rejected
claim, duplicates of a comet track, and bright (20 sigma+) unreported tracks
are left out as ambiguous. The owner's comet labels count as comets; noise
labels are not needed. Features are what the detector knows at its fifth hit
(start radius, speed for that radius, heading, brightness and trend, shape,
straightness); the model is a logistic regression scored leave-one-period-out,
and the smallest feature set that passes the least noise at full recall wins.
Sources are the scored runs under `results/<camera>/` and the live watcher's
archive (`data/live/<camera>/archive/YYYY-MM/<id>.json`, one file per candidate
once it leaves the dashboard), so the set grows by itself and a track reported
after the fact changes side at the next training. Output:
`results/<camera>/learn/README.md` and `model.json`.

A comet label also holds the object in the top list until the label is
cleared, through the tracker rejecting it, the 24 h display cutoff and the
tracker's own pruning: the watcher keeps a copy of its last summary in
`data/live/<camera>/held/` and shows that when the tracker has let it go
(the card says so). Clearing the label drops only the hold; an object that
is still a live watch stays by its own rules.

The export also writes it as `field.gif` (3-map stack, black point, ring)
with its own section in the note. The link to share is the package's
`README.md` on GitHub, which renders the moment the push lands. (A GitHub
Pages copy with its own link preview was tried from 2026-10-31 to 11-07 and
dropped: the Pages build, legacy or through Actions, sat for minutes at
GitHub's end and served stale copies for ten more.)

Full field: the "full field" button next to it asks `/api/movie` with
`full=1` for the frame at the camera's full resolution, cropped the way the
hunters frame their movies: the object's quadrant, its corner nearest the Sun
`FIELD_CLEAR_X` (8) half-res px across and `FIELD_CLEAR_Y` (56) down from the
Sun, which is where Chris Wage's CCOR-1 frame starts (registered on ours
through the comet's positions: 15 and 111 camera px, 1053 x 729 camera px in
all), so the arm's edge, the occulter's limb and the streamer bases are in
for context (at 100 px on each axis, until 2026-11-09, ours showed 184 camera
px less of the inner corona),
running out to the field edge on the object's side (at most `FIELD_MAX` = 640
half-res px) and always holding the path with a `FIELD_MARGIN` of 50 px
(`_field_crop`; one PNG per frame, the last `FULL_FRAMES` = 120 frames, 30 h,
up to the newest hit; `crop` in the movie's JSON is the offset of the crop in
the full-res frame). Each raw frame has the pixelwise median of the good
frames over the `FIELD_BG_HOURS` (24) before it subtracted, every
`FIELD_BG_STEP` (4) frames: the static corona and glare cancel to zero,
streamers show bright where they have moved to and dark where they were, and
stars, the Moon and the object stay. No stacking (shifting along one object's
motion smears everything else; stacking stays in the zoomed patch, where it
helps a faint comet show), no smoothing, and one linear scale from `FIELD_LO`
(-2.2) to `FIELD_HI` (22.8) in units of the noise in the outer half of the
view, so the sky is near black and the grain thickens toward the Sun. Above
10 the scale runs straight in log(value) through `FIELD_SHOULDER`: the Moon's
earthlit face (92 to 553 on CCOR-1, 2026-11-08) gets 100 grey levels around
mid-grey so its maria show, bright stars sit at 124 to 150 instead of white,
and a comet's head is white. Both movies run `FOLLOW_FRAMES` (8, two hours)
past the last hit, the marker carried along the path, and are cached per
count of those frames so a quiet track's movie still grows. This is
Chris Wage's recipe; compared frame for frame with his CCOR-1 movie of
2026-11-08 it has the same zero level and grain. (Until 2026-11-09 the view
was half resolution, the last 32 frames, minus the 24-h minimum, divided by
the noise per radius and smoothed: a grey haze that burned out near the
occulter, and a movie too short to hold the Moon's pass of that morning.)
The marker on both movies sits on the object's measured positions
(interpolated between hits, carried along the last `END_HITS` = 8 hits'
straight-line fit beyond them): one straight line through a 28-hour track sat
up to 29 px off, since a Kreutz comet speeds up as it falls. The zoomed patch
is fixed on the sky and holds the whole path, every frame up to `MAX_FRAMES`
(120): at zoom 4 while the path fits a ~580 px tile (290 camera px), else at
zoom 2 or 1 (`ZOOMS`; a 28-hour track is 940 camera px long), and only a path
too long even for zoom 1 is cut to its newest stretch. The maps are added
along the measured path, not one velocity.

The export's GIFs carry every frame of both movies. The field GIF is binned
2x2 and written in `FIELD_GIF_LEVELS` (16) grey levels: its frames are noise,
which LZW cannot pack (170 KB a frame at 254 levels, 45 KB at 16, nothing
lost to the eye), so 120 frames are 5 MB. The note's positions table has an
"our status" column (`export.status_changes`): the frame on which the track
became a spark, was first followed (third hit), and each tier the first time
it stood higher than before (steady, watch, ALERT; the highest of several
reached on one frame). Every tag the owner gave it is there too, read from
`labels.jsonl` so a later clear does not erase it, with its clock time, on
the newest frame that had reached us when it was given.
A timestamp is written into each frame's corner (`stamp`, a 5x7
bitmap font). One marker on both movies and both GIFs: a circle on the
object, yellow on a frame with a measured hit, dim and dashed where the
position is only carried along the path (before the first hit, across a gap,
after the last); click the picture for 1:1. Same scrub controls; the field plays
at 6 frames/s by default, the black point slider goes below zero and defaults
to the scale's own -2.2 sigma here, 2 sigma for the patch.

Movie: the detail view's "build hi-res movie" button asks `/api/movie` for a
fixed patch of sky (significance maps, the last 8 or 3 frames added along the
fitted motion, 2× camera resolution) and plays it with a black-point slider
and a ring on the fitted path; each frame reports the sigma in the ring against
the patch's brightest 0.1%. Built on demand under `data/live/<camera>/movies/`.

Export: "export for a hunter" (`POST /api/export`) writes
`results/<camera>/live-finds/<id>/` — `README.md` (the note in the Sungrazer
form's layout with the convention and caveats), `movie.gif` (the movie,
animated, standard-library GIF writer in `gif.py`), `sheet.png`, `thumb.png` —
commits it and pushes, and returns the GitHub link to share. The VM pushes with
a write deploy key (`~/.ssh/id_ed25519`, push URL `git@github.com:...`);
without one the files are still served at `/finds/<camera>/<id>/`.

Second camera: everything above runs for CCOR-2 (SWFO-L1, level 1A, 24.3"/px
against CCOR-1's 19.3"/px) with `--camera ccor2`. Pixel thresholds set on
CCOR-1 (glare radius, arm band, speeds, the steady tier's radius) are scaled
by `cameras.px_scale`; the nominal Sun position is per camera; the velocity
envelope borrows the other camera's Kreutz reports, converted through both
WCS (`solar.py`), when a camera has too few of its own; the arm is above the
Sun on CCOR-2 (and on CCOR-1 before the 2026 yaw flip), so the `pylon` rule
takes either side. CCOR-2 has no Earth transits (it sits at L1). Its level-1A
pipeline shifts every image to centre the Sun (`SHIFT_X`/`SHIFT_Y` in the
header, fractional pixels), so detector defects wander across the frames by a
few px over hours and the 16-frame background cannot remove them; stage A
builds a defect map in detector coordinates from 40 raw frames (pixels
persistently outside 0.95-1.05x their local level once the shift is undone,
the ratio's scatter being under 1%; connected blobs over 100 px are the rim
and field edge and are dropped; the shift's sign convention is found by
trying all four) and blanks it, grown by 3 px, in every frame's significance
map (`frames.load_defects`). The raw pixels are left alone: a wandering
defect is somewhere else in every background frame, so the median background
and the star model are immune, and NaN holes would each cost an edge guard.
The same build gives a gain map (`flat.npy`): the median, in detector
coordinates, of each pixel's ratio to its local level. Level 1A is not
flat-fielded, and the detector's pixel-to-pixel gain pattern wanders with the
shift like the defects do; 65% of CCOR-2's 8-sigma map pixels sat where that
pattern deviates over 2%, against 7% of random pixels. Every frame is divided
by the gain map placed onto it (bilinear) before anything else.
Its field edge glows further in than CCOR-1's, so its edge guard is 16 px
(`EDGE_GUARD_BY_CAMERA`) against 6, and because its rim is a ragged scatter of
dropped pixels rather than a solid NaN border (the guard stops ~5 px short of
the dropouts while the glow ridge runs 20-50 px further in, with 100-300
8-sigma pixels per 10k against ~1 inside), the map is also blanked beyond
`field_radius` = 1000 px from the nominal Sun (about 25 solar radii; a
3-hit track was born on the ridge at 1064 px on 2026-11-04). Its `OFFSUNDR`
is not arcsec (~1.8e5 on every frame), so that test is CCOR-1 only.

Deployment: `deploy/` has systemd units (`kreutz-watch@ccor1`,
`kreutz-watch@ccor2`, `kreutz-serve`) that run as the repo owner with
`Restart=always`. Stage A's backfill runs `$KREUTZ_WORKERS` processes
(default: cores minus one, at most 6); each needs ~0.9 GB. The unit sets 3
for the 8 GB VM, where two watchers also hold ~0.9 GB each of resident stage A
(on the earlier 3.9 GB VM three workers got OOM-killed; 2 was the limit). To deploy a
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

### Other movers: obvious objects off the Kreutz lane

Stages B and C only look along the Kreutz lane. `detect/movers.py` follows
the objects that need no search: bright enough to stand out of one frame's
map (`MOVER_PEAK` = 6 sigma, outside the glare, the arm and the edges), linked
frame to frame at a constant velocity in any direction (2 to 16 px/frame,
`LINK_RADIUS` 4 px, one frame may be missed), `MOVER_HITS` = 6 hits with a
median of `MOVER_SIGMA` = 7, and clean on the tracker's artefact rules
(stationary, star-drift, glare-zone, pylon, edge, extended) plus `saturated`
(a median at the map's ceiling is the Moon's rim or a planet). The live
watcher runs it over the tracked span each cycle (`live.other_tracks`; peaks
cached per frame under `movers/`), drops the movers the Kreutz lane already
follows, and gives the rest the tier `other`: a card in their own section,
sheet, movies and export like any track, and no alert.

Over the 32 days of local maps (2025-07-16..24, 2025-08-17..31,
2026-10-16..24) it found 16 movers, every one a real moving object: Kreutz
comets, the Moon's rim twice (before `saturated`), and the confirmed Kracht II
of 2025-08-26 (11 hits at 6 to 10 sigma, moving up and right at 3.4 px/frame,
which the lane rejects as outbound). It is not a search for other comets:
of the eight confirmed non-Kreutz comets of 2025-08-21..30 only two were
bright enough per frame (7 to 9 sigma), the other six sat at 3 to 6 sigma and
would need stacking along their own motion.

### Reported, not followed here

`live.missed_reports`: every hunter's report on this camera within the
tracked span (48 h, a day longer than tracks stay on the dashboard, so a miss
can be looked into) that no track of ours answers for becomes a card of tier
`missed`. Its hits are the report's own fitted path laid over our
frames (2 frames before its first position to 8 after its last), each with
the single-frame significance we measure there, so the sheet, the movies and
the export show what we had. `missed.why` is the reason: the rule that
rejected our track of it, or "never made a track here: n of m frames reach
6 sigma at its position, brightest b". A report is answered for when a
followed track, one archived in the last 72 h, or any unrejected candidate
of the span was matched to it or lies on its path (a card names one report; `report_ids` has all it matches). A
miss the other CCOR camera follows is hidden behind that camera's card.

### Following an object until it is gone

A candidate is closed after `MAX_MISSES` (2) frames without a hit, and pieces
up to `CONTINUE_FRAMES` (6) apart on one path are joined afterwards. Two
things keep that from cutting short an object that matters (tracker `c6`,
2026-11-09):

- **Sticky tracks.** A track at the dashboard's alert grade (`STICKY_HITS` = 4
  hits, `STICKY_SIGMA` = 15) or matched to a hunter's report (the live watcher
  sets `sticky` on its open piece every cycle) stays open through
  `STICKY_MISSES` (8) frames without a hit, two hours, with the match radius
  growing `COAST_SLOP` (0.5) px per coasted frame.
- **Blind frames.** A frame whose map is blank at a candidate's predicted
  place (`stack.seen_fn`: under half of the 5 x 5 map px there finite) is not
  a miss, for up to `BLIND_FRAMES` (8) in a row. CCOR-1's Earth-glare frames
  (04:15 to 05:15 UTC daily) blank most of the map: 20261109T0130_1740_1790,
  17 sigma, was closed at 04:30 that way and stood at 9 sigma on its path
  again at 05:30.

Tracks that take turns on one object, their hits in alternate frames and
never a common time, are merged too (`track._interleaved`: 2 hits of either
within `MERGE_RADIUS` of the other's path between its own hits; tracker `c7`).
On 2026-11-11 a 110-sigma CCOR-2 comet came out as two alert cards that way.
An object gets one alert: one is not sent when a piece of the same object,
or its track on the other CCOR camera, has had it, and an alert names the
other camera's track when there is one.

`Tracker.snapshot(since)` returns the merged tracks with a hit since `since`
(the dashboard's `SHOW_H`), and a track that is not rejected takes its older
pieces with it: filtered piece by piece, a comet followed for 28 h lost the
half before a gap once that half was a day old and came back under a new id
with 67 of its 110 hits. On the 2026-10-17..24 scorecard `c6` finds the same
5 of 5 at the same times as `c5`, with 277 unmatched tracks against 282, and
follows the 10-19 comet for 82 hits against 69.

### Rejection rules and why each exists

A candidate track is rejected (kept in the output, marked `rejected:<rule>`) if:

| rule | test | why |
|---|---|---|
| `stationary` | speed < 2 px/frame | Things that don't move relative to the Sun are residual background (glare, hot pixels, slowly changing corona). Slowest confirmed comet: 3 px/frame. |
| `star-drift` | velocity within 1.5 px/frame of the measured star drift | Imperfectly subtracted stars move together at the drift velocity. |
| `outbound` | moving away from the Sun (cos of angle to the Sun direction < 0) | Kreutz comets are seen falling inward. Cost: 6 of 106 confirmed CCOR-1 reports moved away from the Sun: 5 Kracht II / non-group, 1 Kreutz moving nearly sideways. |
| `off-envelope` | direction of motion, or signed miss distance of the path from the Sun, outside this month's Kreutz envelope (+15°, +80 px margins) | All Kreutz comets share one orbit, so in a given month they arrive from one side on nearly parallel paths; the side rotates through the year (Mar: from the left moving right; Jul: lower right moving up-left; Oct: right moving left). Things entering from anywhere else are not Kreutz comets: in Oct 2026 that included tracks entering at the left edge moving right. Built from confirmed reports (±1 month) where they exist; NRL has posted no confirmations since Aug 2025, so Oct–Jan use that month's pending hunter claims trimmed to the 10–90th percentiles, marked PROVISIONAL in the output. Cost: comets of other groups (Kracht, Meyer, non-group: 10 of 116 confirmed CCOR-1 reports). |
| `glare-zone` | median distance from the Sun < 320 px | Variable glare at the occulter rim dominated the false positives (250 of 452 surviving tracks on 2025-07-17). Confirmed comets were never reported inside 392 px, and we want them earlier than that anyway. |
| `pylon` | median hit within 110 px of the pylon axis (the Sun's x), either side | The arm's shadow edge is the steepest gradient in the frame, so a sub-pixel shift of the arm against the 16-frame background leaves a bright line just outside stage A's mask. Being vertical, it stacks onto itself at any vertical velocity, which is inside the Kreutz envelope for objects directly below the Sun. On 2026-11-01 the edge went unstable after the Earth transit (3–4× the usual 8σ pixels along it) and produced 50 chains in 12 h, two of them promoted and one alerted. Cost: none seen; confirmed comets start 400+ px from the axis and reach it only inside the glare zone. |
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

