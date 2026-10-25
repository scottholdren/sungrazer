"""`kreutz reports sync`: scrape sungrazer.nrl.navy.mil into data/reports.sqlite.

Steps
  1. Walk the plain-text report listing, filtered by camera and image date, 100 per page.
  2. Fetch each report's own page once (cached forever under data/cache/) for the
     coordinate origin, image size and "Time Referenced" link.
  3. Fetch every confirmation post linked from /site_updates (always re-fetched;
     there are only a handful and they can be edited).
  4. Recompute parent links and statuses.
The whole thing is gated to once per 15 minutes unless --force.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from .. import cameras, net, paths
from . import db, parse

BASE = "https://sungrazer.nrl.navy.mil"
CACHE = paths.CACHE / "sungrazer"


def sync(cams: list[str], since: date = date(2025, 1, 1), force: bool = False) -> None:
    allowed, wait = net.poll_allowed("sungrazer", force=force)
    if not allowed:
        print(f"last Sungrazer sync was <15 min ago; next allowed in {wait / 60:.1f} min (use --force)")
        return
    con = db.connect()

    for cam in cams:
        n = crawl_listing(con, _cam(cam), since)
        print(f"{cam}: {n} reports in listing since {since}")
        con.commit()

    todo = [r["report_id"] for r in con.execute("SELECT report_id FROM reports WHERE page_fetched = 0")]
    print(f"fetching {len(todo)} report pages not yet cached")
    for i, rid in enumerate(todo, 1):
        page = report_page(rid)
        db.upsert_report(con, parse.parse_report_page(page, rid), from_page=True)
        if i % 50 == 0:
            con.commit()
            print(f"  {i}/{len(todo)}", flush=True)
    con.commit()

    for slug in confirmation_slugs():
        page = net.get(f"{BASE}/{slug}").text
        confs = parse.parse_confirmations(page)
        db.upsert_confirmations(con, confs, slug)
    con.commit()

    db.recompute_status(con)
    db.rebuild_frames(con)  # cheap; keeps positions in step with coords.report_mapping
    con.commit()
    for row in con.execute("SELECT camera, status, COUNT(*) n FROM reports GROUP BY 1, 2 ORDER BY 1, 2"):
        print(f"  {row['camera']:<6} {row['status']:<10} {row['n']}")
    nconf = con.execute("SELECT COUNT(*), COUNT(report_id) FROM confirmations").fetchone()
    print(f"confirmations: {nconf[0]} rows, {nconf[1]} linked to a synced report")


def _cam(cam_id: str) -> str:
    return cameras.get(cam_id).sungrazer_name


def crawl_listing(con, sungrazer_camera: str, since: date) -> int:
    seen, page = set(), 0
    while True:
        params = {
            "field_camera_value": sungrazer_camera,
            # The site's "between" filter ignores a min without a max.
            "field_report_date_value[min]": since.isoformat(),
            "field_report_date_value[max]": (date.today() + timedelta(days=1)).isoformat(),
            "items_per_page": "100",
            "page": str(page),
        }
        rows = parse.parse_listing(net.get(f"{BASE}/plain-text-reports", params=params).text)
        new = [r for r in rows if r.report_id not in seen]
        if not new:
            return len(seen)
        for r in new:
            db.upsert_report(con, r, from_page=False)
            seen.add(r.report_id)
        page += 1


def report_page(report_id: str) -> str:
    """HTML of a report page, from cache if we've ever fetched it."""
    path = CACHE / "reports" / f"{report_id}.html"
    if path.exists():
        return path.read_text()
    html = net.get(f"{BASE}/report/{report_id.replace('_', '')}").text
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html)
    return html


def confirmation_slugs() -> list[str]:
    """Slugs like 'confs-Jul-Aug2025' of confirmation posts listed on /site_updates."""
    slugs, page = [], 0
    while True:
        html = net.get(f"{BASE}/site_updates", params={"page": str(page)} if page else None).text
        found = [s for s in re.findall(r'href="/(confs-[^"#?]+)"', html) if s not in slugs]
        if not found:
            return slugs
        slugs.extend(dict.fromkeys(found))
        if not re.search(rf'href="\?page={page + 1}"', html):
            return slugs
        page += 1
