"""Export one track the way a Sungrazer hunter would want to receive it, and
publish it to the GitHub repo so there is a link to share.

results/<camera>/live-finds/<id>/
  README.md    the note: what we saw, the convention, the caveats, the positions
               in the form's own "HHMM x y" layout; GitHub renders it with the images
  sheet.png    the contact sheet (bottom row = 8 frames stacked along the motion)
  movie.gif    the hi-res movie, animated, ring on the fitted path
  thumb.png    the stacked cutout around the newest hit
The dashboard serves the folder at /finds/<camera>/<id>/ and, when the host can
push (the VM has a write deploy key), commits it and returns the GitHub link.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import gif, movie, paths
from .inspect import read_png

LANE = {"ccor1": ("Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, "
                  "and head within ~20 deg of the Sun"),
        "ccor2": ("the two Kreutz comets seen in CCOR-2 so far, 2026-11-02 and 11-07, arrived from the lower right, "
                  "position angle -45 to -55 deg, heading within ~10 deg of the Sun")}
LABEL = {"ccor1": "CCOR-1", "ccor2": "CCOR-2"}
BLACK_SIGMA = 2.5  # the patch GIF's black point
FIELD_BLACK_SIGMA = -2.2  # the field GIF's: the movie's own, zero at 9% grey
RING_R = 7         # half-res px


def export_track(camera: str, d: dict, latest: dict | None, root: Path, publish_it: bool = True) -> dict:
    with movie.build_lock():
        return _export_track(camera, d, latest, root, publish_it)


def _export_track(camera: str, d: dict, latest: dict | None, root: Path, publish_it: bool = True) -> dict:
    out = finds_dir(camera) / d["id"]
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for src, name in ((d.get("sheet"), "sheet.png"), (d.get("thumb"), "thumb.png")):
        if src and (root / src).exists():
            shutil.copyfile(root / src, out / name)
            files.append(name)
    mv = None
    try:
        mv = movie.movie(camera, d, root / camera / "movies")
        write_movie_gif(root / mv["png"], mv, out / "movie.gif")
        files.append("movie.gif")
    except Exception as e:  # no processed frames yet: the note still goes out
        mv = {"error": str(e)}
    fm = None
    try:
        fm = movie.movie(camera, d, root / camera / "movies", stack=3, full=True)
        write_field_gif(root, fm, out / "field.gif")
        files.append("field.gif")
    except Exception as e:
        print(f"full-field gif for {d['id']} failed: {e}", flush=True)
    text = note(camera, d, latest, mv, files)
    (out / "README.md").write_text(text)
    files.insert(0, "README.md")
    url = publish(out, d["id"]) if publish_it else None
    return {"text": text, "url": url,
            "files": [{"name": f, "url": f"finds/{camera}/{d['id']}/{f}"} for f in files]}


def finds_dir(camera: str) -> Path:
    return paths.ROOT / "results" / camera / "live-finds"


SHEET_GIF_ZOOM = 2    # the sheet's tiles are 147 px; doubled they read on a phone
SHEET_GIF_HOLD = 5    # the newest frame is shown this many times as long


def write_sheet_gif(sheet_png: Path, columns: list[str], out: Path) -> None:
    """The contact sheet as an animation, for an alert: one frame per column, its three
    tiles side by side (the raw frame around the object, that frame's significance map,
    the last 8 maps stacked along the motion), the frame's time stamped on it, resting on
    the newest."""
    from . import contact

    sheet = read_png(sheet_png)
    T, gap = (2 * contact.HALF + 1) * contact.SCALE, 4
    n = min(len(columns), (sheet.shape[1] - gap) // (T + gap))
    z, bar = SHEET_GIF_ZOOM, 22
    frames = []
    for k in range(n):
        x0 = gap + k * (T + gap)
        img = np.zeros((bar + T * z, 3 * T * z + 2 * gap * z), np.uint8)
        for r in range(contact.ROWS):
            tile = sheet[gap + r * (T + gap):gap + r * (T + gap) + T, x0:x0 + T]
            img[bar:, r * (T + gap) * z:r * (T + gap) * z + T * z] = np.repeat(np.repeat(tile, z, 0), z, 1)
        name = columns[k][9:24] if len(columns[k]) > 24 else columns[k]
        movie.stamp(img, f"{name[:4]}-{name[4:6]}-{name[6:8]} {name[9:11]}:{name[11:13]} UTC", x=4, y=4)
        frames.append(gif.gray_to_index(img.astype(np.float32)))
    if not frames:
        raise ValueError("the sheet has no columns")
    out.parent.mkdir(parents=True, exist_ok=True)
    gif.write_gif(out, frames + [frames[-1]] * (SHEET_GIF_HOLD - 1), delay_cs=60)


def mark(idx: np.ndarray, xx: np.ndarray, yy: np.ndarray, x: float, y: float, r: float, hit: bool) -> None:
    """The one marker of every movie: a circle of radius r on the object, yellow on a
    frame with a measured hit; where the position is only carried along the path
    (before the first hit, across a gap, after the last) it is dim and dashed."""
    ring = np.abs(np.hypot(xx - x, yy - y) - r) < 1.2
    if not hit:
        ring &= np.floor(np.arctan2(yy - y, xx - x) / (np.pi / 8)) % 2 == 0
    idx[ring] = gif.YELLOW if hit else gif.DIM


def write_movie_gif(sheet_png: Path, mv: dict, out: Path) -> None:
    """The movie's frames with a 2.5-sigma black point and the marker, as an animated GIF."""
    sheet = read_png(sheet_png)
    T, cols, z = mv["tile"], mv["cols"], mv["zoom"]
    b = BLACK_SIGMA / mv["range_sigma"] * 255
    yy, xx = np.mgrid[0:T, 0:T]
    frames = []
    for k, f in enumerate(mv["frames"]):
        r, c = divmod(k, cols)
        tile = sheet[r * T:(r + 1) * T, c * T:(c + 1) * T].astype(np.float32)
        tile = np.where(tile <= b, 0, (tile - b) * 255 / (255 - b))
        idx = gif.gray_to_index(tile)
        mark(idx, xx, yy, f["x"], f["y"], RING_R * z, f["hit"])
        frames.append(idx)
    gif.write_gif(out, frames, delay_cs=35)


FIELD_GIF_LEVELS = 16  # grey levels in the field GIF. Its frames are noise, which LZW cannot pack: 170 KB a frame
#                        at 254 levels, 45 KB at 16, with nothing lost to the eye at this size. Every frame of the
#                        movie goes in (120 frames = 5 MB; they were thinned to 32 until 2026-11-09), at half the
#                        movie's resolution.


def write_field_gif(root: Path, fm: dict, out: Path) -> None:
    """The field movie as an animated GIF with the marker: every frame, binned 2x2,
    in FIELD_GIF_LEVELS grey levels."""
    h, w = fm["height"] // 2, fm["width"] // 2
    yy, xx = np.mgrid[0:h, 0:w]
    lo, hi = fm.get("lo_sigma", 0.0), fm.get("hi_sigma", fm["range_sigma"])
    b = max((FIELD_BLACK_SIGMA - lo) / (hi - lo) * 255, 0.0)
    step = 254 // FIELD_GIF_LEVELS
    frames = []
    for p, f in zip(fm["pngs"], fm["frames"]):
        img = read_png(root / p).astype(np.float32)[:2 * h, :2 * w].reshape(h, 2, w, 2).mean(axis=(1, 3))
        if b > 0:
            img = np.where(img <= b, 0, (img - b) * 255 / (255 - b))
        idx = gif.gray_to_index(img)
        idx = np.minimum(idx // step * step + step // 2, 253).astype(np.uint8)
        mark(idx, xx, yy, f["x"] / 2, f["y"] / 2, fm["ring_r"] / 2, f["hit"])
        frames.append(idx)
    gif.write_gif(out, frames, delay_cs=15)


def status_changes(camera: str, d: dict) -> dict[str, list[str]]:
    """hit time -> what the track became on that frame, on our side: spark (two hits
    of SPARK_SIGMA), following (its third hit: a card on the dashboard), then each
    tier the first time the track stood higher than before (steady < watch < ALERT, as
    `live` defines them; of several reached on one frame, the highest), and every tag
    the owner gave it, with its clock time, on the newest hit whose frame had reached
    us by then."""
    from . import live, score

    hits = [{"frame_time": h["t"], "x": h["x"], "y": h["y"], "sig": h["sig"]} for h in d["hits"]]
    out: dict[str, list[str]] = {}

    def at(t: str | None, what: str) -> None:
        if t:
            out.setdefault(t, []).append(what)

    if len(hits) >= 2 and min(hits[0]["sig"], hits[1]["sig"]) >= live.SPARK_SIGMA:
        at(hits[1]["frame_time"], "spark")
    if len(hits) >= 3:
        at(hits[2]["frame_time"], "following")
    tiers = [(live._steady_at(hits, d.get("sun_xy"), camera), 1, "steady"),
             (live._fires_at(hits, *live.TIERS["watch"]), 2, "watch"),
             (live._fires_at(hits, *live.TIERS["alert"]), 3, "ALERT")]
    best = 0
    for t in sorted({t for t, _, _ in tiers if t}):
        rank, name = max((r, n) for tt, r, n in tiers if tt == t)
        if rank > best:
            at(t, name)
            best = rank
    lat_s = score.latency_s(camera)[0]
    for when, label in live.label_history(camera, d["id"]):
        w = datetime.fromisoformat(when)
        seen = [h["frame_time"] for h in hits if datetime.fromisoformat(h["frame_time"]).timestamp() + lat_s <= w.timestamp()]
        at(seen[-1] if seen else hits[0]["frame_time"], f"tag cleared {w:%H:%M}" if label == "clear" else f"tagged {label} {w:%H:%M}")
    return out


def repo_url() -> str | None:
    """The GitHub repo this checkout pushes to, as a web URL, or None outside one."""
    if not (paths.ROOT / ".git").exists():
        return None
    try:
        remote = subprocess.run(["git", "-C", str(paths.ROOT), "remote", "get-url", "origin"], check=True,
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    return remote.removesuffix(".git").replace("git@github.com:", "https://github.com/") if "github.com" in remote else None


def finds_url(camera: str) -> str | None:
    """Where this camera's hunter packages are listed on GitHub: what an alert on the phone
    opens, since the package itself follows a minute or two later."""
    repo = repo_url()
    return f"{repo}/tree/main/results/{camera}/live-finds" if repo else None


def publish(folder: Path, tid: str) -> str | None:
    """Commit the folder and push; the GitHub link to its README, or None if this
    host cannot push (no key, no network, not a checkout)."""
    if os.environ.get("KREUTZ_NO_PUBLISH") or not (paths.ROOT / ".git").exists():
        return None
    git = ["git", "-C", str(paths.ROOT)]
    try:
        rel = folder.relative_to(paths.ROOT)
        subprocess.run(git + ["add", str(rel)], check=True, capture_output=True, timeout=30)
        staged = subprocess.run(git + ["diff", "--cached", "--quiet", "--", str(rel)], capture_output=True, timeout=30)
        if staged.returncode != 0:  # something to commit
            subprocess.run(git + ["commit", "-q", "-m", f"Live find {tid}: hunter's package"],  # as the repo's own user
                           check=True, capture_output=True, timeout=30)
        subprocess.run(git + ["pull", "-q", "--rebase"], check=True, capture_output=True, timeout=120)
        subprocess.run(git + ["push", "-q"], check=True, capture_output=True, timeout=120)
        remote = subprocess.run(git + ["remote", "get-url", "origin"], check=True, capture_output=True,
                                text=True, timeout=30).stdout.strip()
        branch = subprocess.run(git + ["rev-parse", "--abbrev-ref", "HEAD"], check=True, capture_output=True,
                                text=True, timeout=30).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        err = getattr(e, "stderr", b"") or b""
        print(f"publish failed: {e} {err.decode(errors='replace')[:300]}", flush=True)
        return None
    repo = remote.removesuffix(".git").replace("git@github.com:", "https://github.com/")
    return f"{repo}/blob/{branch}/{rel.as_posix()}/README.md"


def note(camera: str, d: dict, latest: dict | None, mv: dict | None, files: list[str] = ()) -> str:
    hits = d["hits"]
    first, last = datetime.fromisoformat(hits[0]["t"]), datetime.fromisoformat(hits[-1]["t"])
    span = f"{first:%Y-%m-%d %H:%M} to {last:%H:%M}" if last.date() == first.date() else f"{first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M}"
    vx, vy = d["velocity"]
    sigs = [h["sig"] for h in hits]
    trend = ("brightening" if sigs[-1] > sigs[0] * 1.3 else "fading" if sigs[-1] < sigs[0] / 1.3 else "steady")
    L = [f"# {LABEL.get(camera, camera)} candidate {d['id']}", "",
         f"**{d['n_hits']} frames, {span} UTC, peak {max(sigs):.0f} sigma, {trend}.**", "",
         f"- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); "
         f"status {d['status']}" + (f", {d['tier']} tier since {datetime.fromisoformat(d['tier_at']):%H:%M} UTC" if d.get("tier") and d.get("tier_at") else "") + ".",
         f"- Motion ({vx:+.1f}, {vy:+.1f}) px per 15-min frame, {math.hypot(vx, vy):.1f} px/frame."]
    if latest and latest.get("sun_xy"):
        sx, sy = latest["sun_xy"]
        x, y = hits[-1]["x"], hits[-1]["y"]
        r = math.hypot(x - sx, y - sy)
        v = math.hypot(vx, vy)
        head = math.degrees(math.acos(max(-1.0, min(1.0, (vx * (sx - x) + vy * (sy - y)) / (r * v + 1e-9))))) if v else float("nan")
        pa = math.degrees(math.atan2(-(y - sy), x - sx))
        L.append(f"- Last position {r:.0f} px from the Sun at position angle {pa:.0f} deg, heading {head:.0f} deg off the Sun direction ({LANE.get(camera, LANE['ccor1'])}).")
    if d.get("missed"):
        L.append(f"- NOT followed by the detector: {d['missed']['why']}. The positions below are the report's own path "
                 "laid over our frames, with the significance we measure there; the movies follow that path.")
    if d.get("other"):
        L.append("- Not on the Kreutz lane: found by the any-direction linker of bright single-frame peaks "
                 "(`detect/movers.py`), not by the shift-and-stack search. No alert is raised for these.")
    rep = d.get("report")
    if rep:
        al = rep.get("alert_lead_h")
        verdict = (f"our alert came {-al:.1f} h before it" if al is not None and al < 0
                   else f"it came {al:.1f} h before our alert" if al is not None
                   else "we never alerted on it")
        L.append(f"- Matches {rep['reporter']}'s {rep.get('camera', 'CCOR-1')} report `{rep['id']}` (posted {rep['posted']}; {verdict}; "
                 + (f"we were following it {-rep['lead_h']:.1f} h before the post" if rep["lead_h"] < 0 else f"posted {rep['lead_h']:.1f} h before our third hit")
                 + ("; matched through both cameras' WCS in solar coordinates" if rep.get("cross") else "") + ").")
    else:
        L.append("- No CCOR-1 or CCOR-2 report matches it on the Sungrazer site.")
    lab = d.get("label")
    if lab:
        L.append(f"- Owner's label: {lab['label']}.")
    if "movie.gif" in files:
        L += ["", "## Movie", "",
              f"A fixed patch of sky at 2x the camera's resolution: significance maps (stars and corona modelled out), "
              f"the last {mv['stack']} maps added along the fitted motion, black below {BLACK_SIGMA:g} sigma. "
              "The circle sits on the object's measured positions: yellow on a frame with a measured hit, dim and "
              "dashed where it is only carried along the path (before the first hit, across a gap, after the last). "
              "A real object crosses the patch inside the circle; noise does not travel.", "",
              "![movie](movie.gif)"]
    if "field.gif" in files:
        L += ["", "## Full field", "",
              "The object's quadrant from just outside the occulter out to the field edge, up to the newest hit: each frame minus the "
              "median of the previous 24 hours (the static corona and glare cancel; streamers show bright where they have moved to "
              "and dark where they were; stars stay), no stacking, no smoothing, on one linear scale with zero near black, so the "
              "noise shows as grain that thickens toward the Sun. The circle is the same marker as above (yellow on a "
              "frame with a measured hit, dim and dashed otherwise), and the movie runs two hours past the last one. This GIF is every frame at half resolution; the "
              "dashboard plays them at the camera's resolution. A faint object is easier to judge in the stacked patch above.", "",
              "![full field](field.gif)"]
    if "sheet.png" in files:
        L += ["", "## Contact sheet", "",
              "Top: raw frame. Middle: significance map of that frame. Bottom: the detector's view, the last 8 maps "
              "stacked along the motion. Ticks mark the predicted position; a comet is a steady dot on the ticks.", "",
              "![contact sheet](sheet.png)"]
    if "thumb.png" in files:
        L += ["", "![stacked cutout around the newest hit](thumb.png)"]
    L += ["", "## Positions", "",
          "2048x1920 full-resolution frame, origin upper left, x right, y down, 0-based (the Sungrazer 'Upper Left' "
          "convention). They are the centre of the stacked detection predicted from the fitted motion, good to about "
          "a pixel, not per-frame centroids; hunters' measurements tend to fall ~1 px right and up of these.", "",
          "The last column is what the track became here on that frame: spark (two bright hits, before it is a "
          "track), following (third hit), then each of the dashboard's tiers the first time it stood higher than "
          "before (steady, watch, ALERT). The owner's tags carry their clock time and sit on the newest frame that "
          "had reached us when they were given; the rest are frame times, and a frame reaches us 15 to 25 minutes "
          "after it is taken.", "",
          "| date | HHMM | x | y | sigma | our status |", "|---|---|---|---|---|---|"]
    changes = status_changes(camera, d)
    L += [f"| {datetime.fromisoformat(h['t']):%Y-%m-%d} | {datetime.fromisoformat(h['t']):%H%M} | {h['x']:.0f} | {h['y']:.0f} | {h['sig']:.1f} "
          f"| {', '.join(changes.get(h['t'], []))} |" for h in hits]
    L += ["", "Form layout (HHMM x y):", "", "```"] + [f"{datetime.fromisoformat(h['t']):%H%M} {h['x']:.0f} {h['y']:.0f}" for h in hits] + ["```"]
    if mv and "frames" in mv:
        L += ["", "## Movie frames", "",
              "Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. "
              "A real object sits clearly above the tail.", "",
              "| frame | ring | tail | |", "|---|---|---|---|"]
        L += [f"| {f['name']} | {f.get('peak') if f.get('peak') is not None else '-'} | {f.get('tail', '-')} | {'hit' if f['hit'] else ''} |" for f in mv["frames"]]
    elif mv and "error" in mv:
        L += ["", f"(no movie: {mv['error']})"]
    L += ["", f"*Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.*"]
    return "\n".join(L) + "\n"
