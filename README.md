# kreutz — Kreutz sungrazer detector for CCOR coronagraphs

Goal: spot Kreutz-family sungrazing comets in NOAA's CCOR-1 (on GOES-19) and
CCOR-2 (on SWFO-L1) coronagraph images early enough to report them to the
[NRL Sungrazer Project](https://sungrazer.nrl.navy.mil/).

A coronagraph blocks the Sun with a disc (the *occulter*, held by a support arm)
so the faint corona and anything near the Sun becomes visible. Kreutz comets show
up as faint dots that move in a nearly straight line toward the Sun across many
15-minute frames, getting brighter. Stars drift slowly the other way, all together.

Status: **step 1 of 5 — data layer.** We can download frames, inspect them, and
keep a local "answer key" of what human hunters reported and what got confirmed.

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
```

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
`*_Q` flags are True when things are *good*. We flag a frame when any of these is
off: `ISNORMAL`, `DCMPRS_Q`, `IMGBLK_Q`, `EARFOV_Q` (Earth in view), `MONFOV_Q`
(Moon in view), `BORSGT_Q`, `GTTRAC_Q`; or when `E_ECLPSE`/`M_ECLPSE` (Earth/Moon
eclipse state), `BADBLK_N`/`MISBLK_N` are non-zero; door not `OPEN`; `MAJSTORM`
(particle storm); or the image is mostly empty.

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

## Answer key: `data/reports.sqlite`

* `reports`: `report_id`, `reporter`, `report_timestamp` (posting time, UTC),
  `image_date`, `camera` (`ccor1`/`ccor2`), `sighting_type`, `comet_group`,
  `origin`, `image_size`, `time_referenced`/`parent_id` (for follow-ups),
  `endorsements`, `status`, `frames_text` (raw position block as posted).
* `frames`: `report_id, seq, frame_time (UTC), x, y` as typed, `x_ul, y_ul` in
  our convention, and the `raw` line. Unparseable lines are kept with NULLs.
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
