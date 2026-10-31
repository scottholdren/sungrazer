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
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import gif, movie, paths
from .inspect import read_png, write_png

LANE = ("Kreutz comets in CCOR-1 arrive from the lower right, position angle -10 to -45 deg, "
        "and head within ~20 deg of the Sun")
BLACK_SIGMA = 2.5  # the patch GIF's black point
FIELD_BLACK_SIGMA = -2.0  # the field GIF's: below zero, so the noise stays as texture
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
    fm = None
    try:
        fm = movie.movie(camera, d, root / camera / "movies", stack=3, full=True)
        write_field_gif(root, fm, out / "field.gif")
        files.append("field.gif")
        card(root, fm, out / "card.png", camera)
        files.append("card.png")
    except Exception as e:
        print(f"full-field gif for {d['id']} failed: {e}", flush=True)
    text = note(camera, d, latest, mv, files)
    (out / "README.md").write_text(text)
    files.insert(0, "README.md")
    page = page_url(out)
    (out / "index.html").write_text(write_page(d, text, files, page))
    files.insert(1, "index.html")
    repo = publish(out, d["id"]) if publish_it else None
    return {"text": text, "url": page if repo else None, "repo_url": repo,
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


def write_field_gif(root: Path, fm: dict, out: Path) -> None:
    """The full-field movie's frames (one PNG each) with the black point and the ring,
    as an animated GIF; mostly black, so it compresses well despite the size."""
    yy, xx = np.mgrid[0:fm["height"], 0:fm["width"]]
    lo, hi = fm.get("lo_sigma", 0.0), fm.get("hi_sigma", fm["range_sigma"])
    b = max((FIELD_BLACK_SIGMA - lo) / (hi - lo) * 255, 0.0)  # the field keeps its noise as texture
    frames = []
    for p, f in zip(fm["pngs"], fm["frames"]):
        img = read_png(root / p).astype(np.float32)
        if b > 0:
            img = np.where(img <= b, 0, (img - b) * 255 / (255 - b))
        idx = gif.gray_to_index(img)
        ring = np.abs(np.hypot(xx - f["x"], yy - f["y"]) - fm["ring_r"]) < 1.2
        idx[ring] = gif.GREEN if f["hit"] else gif.YELLOW
        frames.append(idx)
    gif.write_gif(out, frames, delay_cs=50)


CARD_W, CARD_H = 1000, 525  # the link-preview image: a crop of the newest full-field frame around the object
_NAVY, _BLUE, _WHITE = np.array([6, 10, 28]), np.array([38, 92, 205]), np.array([255, 255, 255])


def card(root: Path, fm: dict, out: Path, camera: str = "ccor1") -> None:
    """A colour crop around the object on the newest hit frame, ring on, for link
    previews: the raw coronagraph frame (radially flattened, so streamers and the
    occulter show) when it is on disk, else the significance map."""
    k = max((i for i, f in enumerate(fm["frames"]) if f["hit"]), default=len(fm["frames"]) - 1)
    f = fm["frames"][k]
    img = _raw_half(camera, f["name"])
    if img is None:
        img = read_png(root / fm["pngs"][k]).astype(np.float32)
        b = BLACK_SIGMA / fm["range_sigma"] * 255
        img = np.where(img <= b, 0, (img - b) * 255 / (255 - b))
    h, w = img.shape
    x0 = int(np.clip(round(f["x"] - CARD_W / 2), 0, max(w - CARD_W, 0)))
    y0 = int(np.clip(round(f["y"] - CARD_H / 2), 0, max(h - CARD_H, 0)))
    crop = img[y0:y0 + CARD_H, x0:x0 + CARD_W]
    v = (crop / 255.0)[..., None]
    rgb = np.where(v < 0.5, _NAVY + (_BLUE - _NAVY) * (v / 0.5), _BLUE + (_WHITE - _BLUE) * ((v - 0.5) / 0.5))
    yy, xx = np.mgrid[0:crop.shape[0], 0:crop.shape[1]]
    ring = np.abs(np.hypot(xx - (f["x"] - x0), yy - (f["y"] - y0)) - fm["ring_r"] * 1.6) < 1.6
    rgb[ring] = (79, 211, 122)
    write_png(out, np.clip(rgb, 0, 255).astype(np.uint8))


def _raw_half(camera: str, name: str) -> np.ndarray | None:
    """The raw frame `name` (YYYYMMDDTHHMMSS) at half resolution, display-stretched,
    row 0 at the top; None if it is not on disk or unreadable."""
    from .fitsio import read_frame_cached
    from .inspect import stretch

    hits = sorted(paths.frame_dir(camera, name[:8]).glob(f"*{name}*.fits*"))
    if not hits:
        return None
    try:
        d = read_frame_cached(hits[0]).data.astype(np.float64)
        h, w = (d.shape[0] // 2) * 2, (d.shape[1] // 2) * 2
        with np.errstate(all="ignore"):
            small = np.nanmean(d[:h, :w].reshape(h // 2, 2, w // 2, 2), axis=(1, 3))
        return stretch(small).astype(np.float32)
    except Exception as e:
        print(f"card: raw frame {name} unreadable ({e}); using the significance map", flush=True)
        return None


def _repo_slug() -> str | None:
    """owner/repo from the origin remote, or None outside a checkout with a GitHub remote."""
    if not (paths.ROOT / ".git").exists():
        return None
    try:
        remote = subprocess.run(["git", "-C", str(paths.ROOT), "remote", "get-url", "origin"], check=True,
                                capture_output=True, text=True, timeout=30).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", remote)
    return f"{m[1]}/{m[2]}" if m else None


def page_url(folder: Path) -> str | None:
    """Where GitHub Pages serves this folder's index.html (the link to share: it carries
    its own preview image, where a link into the repo shows the repo's card)."""
    slug = _repo_slug()
    if not slug:
        return None
    owner, repo = slug.split("/")
    return f"https://{owner}.github.io/{repo}/{folder.relative_to(paths.ROOT).as_posix()}/"


def write_page(d: dict, text: str, files: list[str], page: str | None) -> str:
    """index.html for the package: the note as HTML plus Open Graph tags so a posted
    link previews with the object's own picture."""
    title = text.splitlines()[0].lstrip("# ").strip()
    summary = next((ln.strip("* ").strip() for ln in text.splitlines() if ln.startswith("**")), "")
    og = [f'<meta property="og:title" content="{_esc(title)}">',
          f'<meta property="og:description" content="{_esc(summary)}">',
          '<meta property="og:type" content="article">', '<meta name="twitter:card" content="summary_large_image">']
    if page:
        og.append(f'<meta property="og:url" content="{_esc(page)}">')
        if "card.png" in files:
            og += [f'<meta property="og:image" content="{_esc(page)}card.png">',
                   f'<meta property="og:image:width" content="{CARD_W}">', f'<meta property="og:image:height" content="{CARD_H}">',
                   f'<meta name="twitter:image" content="{_esc(page)}card.png">']
    css = ("body{background:#0b0e14;color:#e6e6e6;font:15px/1.5 system-ui,sans-serif;max-width:980px;margin:0 auto;padding:16px}"
           "img{max-width:100%;height:auto;display:block;margin:8px 0}table{border-collapse:collapse;font-size:14px}"
           "th,td{border-bottom:1px solid #2a3140;padding:3px 10px;text-align:left}pre{background:#141a25;padding:10px;overflow:auto}"
           "a{color:#9ecbff}h1{font-size:22px}h2{font-size:17px;margin-top:28px;color:#9ecbff}code{background:#141a25;padding:0 4px}")
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            f"<title>{_esc(title)}</title>\n" + "\n".join(og) + f"\n<style>{css}</style></head>\n<body>\n{_md_html(text)}\n</body></html>\n")


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _inline(s: str) -> str:
    s = _esc(s)
    s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="\2">', s)
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", s)


def _md_html(text: str) -> str:
    """The subset of Markdown the note uses: headings, paragraphs, bullets, tables,
    fenced code, images, links, bold, italics, inline code."""
    out, para, items, rows, code = [], [], [], [], None

    def flush():
        nonlocal para, items, rows
        if para:
            out.append(f"<p>{_inline(' '.join(para))}</p>"); para = []
        if items:
            out.append("<ul>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + "</ul>"); items = []
        if rows:
            head, body = rows[0], [r for r in rows[1:] if not re.fullmatch(r"[\s|:-]+", r)]
            cells = lambda r: [c.strip() for c in r.strip().strip("|").split("|")]
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in cells(head)) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in cells(r)) + "</tr>" for r in body) + "</table>")
            rows = []

    for ln in text.splitlines():
        if code is not None:
            if ln.startswith("```"):
                out.append(f"<pre>{_esc(chr(10).join(code))}</pre>"); code = None
            else:
                code.append(ln)
            continue
        if ln.startswith("```"):
            flush(); code = []
        elif ln.startswith("#"):
            flush(); level = len(ln) - len(ln.lstrip("#")); out.append(f"<h{level}>{_inline(ln.lstrip('# '))}</h{level}>")
        elif ln.startswith("- "):
            if para or rows: flush()
            items.append(ln[2:])
        elif ln.startswith("|"):
            if para or items: flush()
            rows.append(ln)
        elif not ln.strip():
            flush()
        else:
            if items or rows: flush()
            para.append(ln)
    flush()
    return "\n".join(out)


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
    if "field.gif" in files:
        L += ["", "## Full field", "",
              "The whole frame at half resolution, the last frames up to the newest hit: each frame minus the "
              "median of the five before it, no stacking, on a linear scale from -2 sigma of the frame's noise "
              "(so the noise shows as texture). For where the object sits in the corona and what else is moving; "
              "a faint object is easier to judge in the stacked patch above.", "",
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
    L += ["", f"*Written {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.*"]
    return "\n".join(L) + "\n"
