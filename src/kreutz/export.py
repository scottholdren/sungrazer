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

LANE = ("Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, "
        "and head within ~20 deg of the Sun")
BLACK_SIGMA = 2.5  # the GIF's black point
RING_R = 7         # half-res px


def export_track(camera: str, d: dict, latest: dict | None, root: Path, publish_it: bool = True) -> dict:
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
    text = note(camera, d, latest, mv, files)
    (out / "README.md").write_text(text)
    files.insert(0, "README.md")
    url = publish(out, d["id"]) if publish_it else None
    return {"text": text, "url": url,
            "files": [{"name": f, "url": f"finds/{camera}/{d['id']}/{f}"} for f in files]}


def finds_dir(camera: str) -> Path:
    return paths.ROOT / "results" / camera / "live-finds"


def write_movie_gif(sheet_png: Path, mv: dict, out: Path) -> None:
    """The movie's frames with a 2.5-sigma black point and the ring, as an animated GIF."""
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
        ring = np.abs(np.hypot(xx - f["x"], yy - f["y"]) - RING_R * z) < 1.2
        idx[ring] = gif.GREEN if f["hit"] else gif.YELLOW
        frames.append(idx)
    gif.write_gif(out, frames, delay_cs=35)


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
            subprocess.run(git + ["-c", "user.name=kreutz live", "-c", "user.email=kreutz-live@stargrazer",
                                  "commit", "-q", "-m", f"Live find {tid}: hunter's package"],
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
    L = [f"# CCOR-1 candidate {d['id']}", "",
         f"**{d['n_hits']} frames, {span} UTC, peak {max(sigs):.0f} sigma, {trend}.**", "",
         f"- Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); "
         f"status {d['status']}" + (f", {d['tier']} tier since {datetime.fromisoformat(d['tier_at']):%H:%M} UTC" if d.get("tier") else "") + ".",
         f"- Motion ({vx:+.1f}, {vy:+.1f}) px per 15-min frame, {math.hypot(vx, vy):.1f} px/frame."]
    if latest and latest.get("sun_xy"):
        sx, sy = latest["sun_xy"]
        x, y = hits[-1]["x"], hits[-1]["y"]
        r = math.hypot(x - sx, y - sy)
        v = math.hypot(vx, vy)
        head = math.degrees(math.acos(max(-1.0, min(1.0, (vx * (sx - x) + vy * (sy - y)) / (r * v + 1e-9))))) if v else float("nan")
        pa = math.degrees(math.atan2(-(y - sy), x - sx))
        L.append(f"- Last position {r:.0f} px from the Sun at position angle {pa:.0f} deg, heading {head:.0f} deg off the Sun direction ({LANE}).")
    rep = d.get("report")
    if rep:
        L.append(f"- Matches {rep['reporter']}'s {rep.get('camera', 'CCOR-1')} report `{rep['id']}` (posted {rep['posted']}, "
                 + (f"we had it {-rep['lead_h']:.1f} h earlier" if rep["lead_h"] < 0 else f"posted {rep['lead_h']:.1f} h before our third hit") + ").")
    else:
        L.append("- No CCOR-1 report matches it on the Sungrazer site.")
    lab = d.get("label")
    if lab:
        L.append(f"- Owner's label: {lab['label']}.")
    if "movie.gif" in files:
        L += ["", "## Movie", "",
              f"A fixed patch of sky at 2x the camera's resolution: significance maps (stars and corona modelled out), "
              f"the last {mv['stack']} maps added along the fitted motion, black below {BLACK_SIGMA:g} sigma. "
              "The ring rides the fitted path: green on frames with a measured hit, yellow before the first hit. "
              "A real object crosses the patch inside the ring; noise does not travel.", "",
              "![movie](movie.gif)"]
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
          "| date | HHMM | x | y | sigma |", "|---|---|---|---|---|"]
    L += [f"| {datetime.fromisoformat(h['t']):%Y-%m-%d} | {datetime.fromisoformat(h['t']):%H%M} | {h['x']:.0f} | {h['y']:.0f} | {h['sig']:.1f} |" for h in hits]
    L += ["", "Form layout (HHMM x y):", "", "```"] + [f"{datetime.fromisoformat(h['t']):%H%M} {h['x']:.0f} {h['y']:.0f}" for h in hits] + ["```"]
    if mv and "frames" in mv:
        L += ["", "## Movie frames", "",
              "Sigma inside the ring on the fitted position vs the brightest 0.1% of the whole patch. "
              "A real object sits clearly above the tail.", "",
              "| frame | ring | tail | |", "|---|---|---|---|"]
        L += [f"| {f['name']} | {f.get('peak') if f.get('peak') is not None else '-'} | {f.get('tail', '-')} | {'hit' if f['hit'] else ''} |" for f in mv["frames"]]
    elif mv and "error" in mv:
        L += ["", f"(no movie: {mv['error']})"]
    L += ["", f"*Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by kreutz live.*"]
    return "\n".join(L) + "\n"
