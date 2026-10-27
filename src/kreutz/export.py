"""Export one track the way a Sungrazer hunter would want to receive it.

Writes, under data/live/<camera>/exports/:
  <id>.txt        a note: what we saw, the convention, the caveats, and the
                  per-frame positions in the form's own "HHMM x y" layout
  <id>_sheet.png  the contact sheet (bottom row = 8 frames stacked along the motion)
  <id>_movie.png  the hi-res movie's frames as a grid (built if it isn't yet)
  <id>_thumb.png  the stacked cutout around the newest hit
The dashboard serves them at /<camera>/exports/<name>.
"""

from __future__ import annotations

import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

from . import live, movie

LANE = "Kreutz comets in CCOR-1 arrive from the lower right (position angle -10 to -45 deg) and head within ~20 deg of the Sun"


def export_track(camera: str, d: dict, latest: dict | None, root: Path) -> dict:
    out = root / camera / "exports"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for src, suffix in ((d.get("sheet"), "_sheet.png"), (d.get("thumb"), "_thumb.png")):
        if src and (root / src).exists():
            shutil.copyfile(root / src, out / f"{d['id']}{suffix}")
            files.append(f"{d['id']}{suffix}")
    mv = None
    try:
        mv = movie.movie(camera, d, root / camera / "movies")
        shutil.copyfile(root / mv["png"], out / f"{d['id']}_movie.png")
        files.append(f"{d['id']}_movie.png")
    except Exception as e:  # no processed frames yet: the note still goes out
        mv = {"error": str(e)}
    text = note(camera, d, latest, mv)
    (out / f"{d['id']}.txt").write_text(text)
    files.insert(0, f"{d['id']}.txt")
    return {"text": text, "files": [{"name": f, "url": f"{camera}/exports/{f}"} for f in files]}


def note(camera: str, d: dict, latest: dict | None, mv: dict | None) -> str:
    hits = d["hits"]
    first, last = datetime.fromisoformat(hits[0]["t"]), datetime.fromisoformat(hits[-1]["t"])
    vx, vy = d["velocity"]
    sigs = [h["sig"] for h in hits]
    trend = ("brightening" if sigs[-1] > sigs[0] * 1.3 else "fading" if sigs[-1] < sigs[0] / 1.3 else "steady")
    span = f"{first:%Y-%m-%d %H:%M} to {last:%H:%M}" if last.date() == first.date() else f"{first:%Y-%m-%d %H:%M} to {last:%Y-%m-%d %H:%M}"
    lines = [f"CCOR-1 candidate {d['id']}: {d['n_hits']} frames, {span} UTC, "
             f"peak {max(sigs):.0f} sigma, {trend}.",
             f"Detected by the kreutz shift-and-stack search (8 frames stacked along the fitted motion); "
             f"status {d['status']}" + (f", {d['tier']} tier since {datetime.fromisoformat(d['tier_at']):%H:%M} UTC" if d.get("tier") else "") + ".",
             f"Motion ({vx:+.1f}, {vy:+.1f}) px per 15-min frame, {math.hypot(vx, vy):.1f} px/frame."]
    if latest and latest.get("sun_xy"):
        sx, sy = latest["sun_xy"]
        x, y = hits[-1]["x"], hits[-1]["y"]
        r = math.hypot(x - sx, y - sy)
        v = math.hypot(vx, vy)
        head = math.degrees(math.acos(max(-1.0, min(1.0, (vx * (sx - x) + vy * (sy - y)) / (r * v + 1e-9))))) if v else float("nan")
        pa = math.degrees(math.atan2(-(y - sy), x - sx))
        lines.append(f"Last position {r:.0f} px from the Sun at position angle {pa:.0f} deg, heading {head:.0f} deg off the Sun direction ({LANE}).")
    r = d.get("report")
    if r:
        lines.append(f"Matches {r['reporter']}'s {r.get('camera', 'CCOR-1')} report {r['id']} (posted {r['posted']}, "
                     + (f"we had it {-r['lead_h']:.1f} h earlier" if r["lead_h"] < 0 else f"posted {r['lead_h']:.1f} h before our third hit") + ").")
    else:
        lines.append("No CCOR-1 report matches it on the Sungrazer site.")
    lab = d.get("label")
    if lab:
        lines.append(f"Owner's label: {lab['label']}.")
    lines += ["",
              "Positions: 2048x1920 full-resolution frame, origin upper left, x right, y down, 0-based (the Sungrazer "
              "'Upper Left' convention). They are the centre of the stacked detection predicted from the fitted "
              "motion, good to about a pixel, not per-frame centroids; hunters' measurements tend to fall ~1 px "
              "right and up of these.", "",
              "  date        HHMM     x     y   sigma"]
    lines += [f"  {datetime.fromisoformat(h['t']):%Y-%m-%d  %H%M}  {h['x']:4.0f}  {h['y']:4.0f}   {h['sig']:.1f}" for h in hits]
    lines += ["", "Form layout (HHMM x y):"] + [f"{datetime.fromisoformat(h['t']):%H%M} {h['x']:.0f} {h['y']:.0f}" for h in hits]
    if mv and "frames" in mv:
        lines += ["", f"Movie (fixed patch, last {mv['stack']} maps stacked along the motion): sigma inside the ring on the "
                  "fitted position vs the brightest 0.1% of the whole patch. A real object sits clearly above the tail.",
                  "  frame            ring   tail"]
        lines += [f"  {f['name']}  {f.get('peak') if f.get('peak') is not None else '-':>5}  {f.get('tail', '-'):>5}{'  hit' if f['hit'] else ''}" for f in mv["frames"]]
    elif mv and "error" in mv:
        lines += ["", f"(no movie: {mv['error']})"]
    lines += ["", "Attached: the contact sheet (top: raw frame, middle: significance map, bottom: 8 maps stacked along "
              "the motion; ticks mark the predicted position), the movie frames as a grid, and the stacked cutout "
              "around the newest hit.",
              f"Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC by kreutz live."]
    return "\n".join(lines) + "\n"
