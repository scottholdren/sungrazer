"""Turn Sungrazer HTML pages into plain Python records. No network here.

Three page types:
  * listing (/plain-text-reports): one row per report, frames as "HHMM x y" lines,
    plus endorsements. Used to enumerate reports.
  * report page (/report/reportxxYYYYMMDDHHMMSS): adds image size, coordinate
    origin ("Upper Left"/"Lower Left") and "Time Referenced" for follow-ups.
    The 14 digits in the id are the posting time in US EASTERN time.
  * confirmation post (/confs-...): fixed-width tables of confirmed comets.

Frame times in reports are just HHMM. The form asks for "the Date of the FIRST
IMAGE", so the first line is on that date and the date rolls forward whenever
the clock goes backwards (2345 -> 0030 is the next day).
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
REPORT_ID = re.compile(r"report_?([a-z0-9]+)_?(\d{14})")


@dataclass
class FramePos:
    seq: int
    frame_time: datetime | None  # UTC; None if the line's time was unreadable
    x: float | None
    y: float | None
    raw: str


@dataclass
class Report:
    report_id: str  # canonical "report_cw_20261023091620"
    reporter: str | None = None
    report_timestamp: datetime | None = None  # when it was posted (UTC), from the id
    image_date: date | None = None
    sighting_type: str | None = None
    comet_group: str | None = None
    camera: str | None = None  # Sungrazer label: CCOR1, CCOR2, C2, C3
    image_size: str | None = None
    origin: str | None = None
    time_referenced: datetime | None = None
    endorsements: list[str] = field(default_factory=list)
    moderation: str | None = None
    frames_text: str = ""
    frames: list[FramePos] = field(default_factory=list)


@dataclass
class Confirmation:
    series: str  # "SOHO", "CCR1", ...
    number: str
    post_time: datetime  # UTC (converted from the Eastern-time list)
    discoverer: str
    telescopes: str
    comet_group: str
    images_of: str


def canonical_id(s: str) -> str:
    m = REPORT_ID.search(s)
    if not m:
        raise ValueError(f"not a report id: {s!r}")
    return f"report_{m.group(1)}_{m.group(2)}"


def eastern_to_utc(naive: datetime) -> datetime:
    """Sungrazer stamps posts in US Eastern local time, not UT.

    The form instructions say UT, but the evidence says otherwise: e.g.
    report_jr_20261024054650 lists frames up to 09:15 UT, so it cannot have been
    posted at 05:46 UT; 05:46 EDT = 09:46 UT fits. Drupal's own "Time Referenced"
    attributes agree (shown 15:08, stored 19:08Z in July). Confirmation-list
    post times use the same local clock as the report ids.
    """
    return naive.replace(tzinfo=EASTERN).astimezone(timezone.utc)


def id_timestamp(report_id: str) -> datetime:
    """report_ss_20250717150817 -> 2025-07-17 19:08:17 UTC (the posting time)."""
    stamp = REPORT_ID.search(report_id).group(2)
    return eastern_to_utc(datetime.strptime(stamp, "%Y%m%d%H%M%S"))


def _text(fragment: str) -> str:
    fragment = re.sub(r"<br\s*/?>", "\n", fragment)
    fragment = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(fragment).strip()


# --- frame lines ---------------------------------------------------------------

# "0800 2021 1375", "08:00 2021 1375", "08:00, 2021.5, 1375", "0800 2021,1375"
_FRAME_LINE = re.compile(
    r"^\s*(\d{1,2}):?(\d{2})(?::\d{2})?\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$"
)


def parse_frames(text: str, first_date: date | None) -> list[FramePos]:
    """Parse the free-text position block. Unparseable lines are kept with None fields."""
    out: list[FramePos] = []
    day = first_date
    prev: time | None = None
    for line in (l.strip() for l in text.splitlines()):
        if not line:
            continue
        seq = len(out)
        m = _FRAME_LINE.match(line)
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            out.append(FramePos(seq, None, None, None, line))
            continue
        t = time(int(m.group(1)), int(m.group(2)))
        if prev is not None and t < prev and day is not None:
            day += timedelta(days=1)
        prev = t
        ft = datetime.combine(day, t, tzinfo=timezone.utc) if day else None
        out.append(FramePos(seq, ft, float(m.group(3)), float(m.group(4)), line))
    return out


# --- listing page ----------------------------------------------------------------

_ROW_SPLIT = re.compile(r'<div class="[^"]*views-row[^"]*">')


def _field(row: str, name: str) -> str | None:
    m = re.search(
        rf'views-field-{name}">(?:<h6[^>]*>.*?</h6>)?<(?:div|span) class="field-content">(.*?)</(?:div|span)>',
        row, re.S)
    return _text(m.group(1)) if m else None


def parse_listing(page_html: str) -> list[Report]:
    out = []
    for row in _ROW_SPLIT.split(page_html)[1:]:
        title = _field(row, "title")
        if not title or not REPORT_ID.search(title):
            continue
        rid = canonical_id(title)
        d = re.search(r'views-field-field-report-date.*?<time datetime="(\d{4}-\d{2}-\d{2})', row, re.S)
        image_date = date.fromisoformat(d.group(1)) if d else None
        frames_text = _field(row, "field-frames") or ""
        endorse = _field(row, "field-endorsements")
        out.append(Report(
            report_id=rid,
            reporter=_field(row, "field-contributor"),
            report_timestamp=id_timestamp(rid),
            image_date=image_date,
            sighting_type=_field(row, "field-sighting-type"),
            comet_group=_field(row, "field-comet-group"),
            camera=_field(row, "field-camera"),
            moderation=_field(row, "moderation-state"),
            endorsements=[e.strip() for e in endorse.split(",") if e.strip()] if endorse else [],
            frames_text=frames_text,
            frames=parse_frames(frames_text, image_date),
        ))
    return out


# --- individual report page -------------------------------------------------------

def _fields(page: str) -> dict[str, list[str]]:
    """Map Drupal field name ('camera', 'frames', ...) -> list of item HTML fragments."""
    out: dict[str, list[str]] = {}
    for m in re.finditer(r'<div class="field [^"]*field-name-(?:field-)?([a-z0-9-]+?)[ "]', page):
        name = m.group(1)
        # The field's markup ends at the next top-level field div.
        nxt = re.compile(r'<div class="field [^"]*field-name-').search(page, m.end())
        chunk = page[m.end(): nxt.start() if nxt else len(page)]
        items = re.findall(r'<div class="field__item">(.*?)</div>', chunk, re.S)
        out.setdefault(name, []).extend(items)
    return out


def parse_report_page(page_html: str, report_id: str) -> Report:
    m = re.search(r"<main.*?</main>", page_html, re.S)
    f = _fields(m.group(0) if m else page_html)

    def one(name):
        return _text(f[name][0]) if f.get(name) else None

    rid = canonical_id(report_id)
    image_date = None
    if f.get("report-date"):
        d = re.search(r'datetime="(\d{4}-\d{2}-\d{2})', f["report-date"][0])
        image_date = date.fromisoformat(d.group(1)) if d else None
    tref = None
    if f.get("time-referenced"):
        # The datetime attribute is the true UTC instant (Drupal converted the
        # Eastern-time value the hunter typed); see eastern_to_utc().
        d = re.search(r'datetime="([^"]+)"', f["time-referenced"][0])
        if d:
            tref = datetime.fromisoformat(d.group(1).replace("Z", "+00:00"))
    frames_text = "\n".join(_text(x) for x in f.get("frames", []))
    return Report(
        report_id=rid,
        reporter=one("contributor"),
        report_timestamp=id_timestamp(rid),
        image_date=image_date,
        sighting_type=one("sighting-type"),
        comet_group=one("comet-group"),
        camera=one("camera"),
        image_size=one("image-size"),
        origin=one("your-0-0-position-"),
        time_referenced=tref,
        frames_text=frames_text,
        frames=parse_frames(frames_text, image_date),
    )


# --- confirmation posts -----------------------------------------------------------

_CONF_HEADER = re.compile(r"^\s*([A-Za-z]+\d?)#\s+Date/Time of Post", re.M)
_CONF_ROW = re.compile(
    r"^\s*(\d{3,5})\s+([A-Z][a-z]{2})\s?(\d{1,2}),\s?(\d{2})\s+(\d{1,2}:\d{2}:\d{2})\s+"
    r"(\S+)\s+(\S+)\s+(\S+)\s+(.*?)\s*$")
_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_confirmations(page_html: str) -> list[Confirmation]:
    """Parse every fixed-width table ("Soho# ...", "CCR1# ...") in a confirmation post."""
    m = re.search(r"<main.*?</main>", page_html, re.S)
    text = _text(re.sub(r"</(p|div|pre|tr|li)>", "\n", m.group(0) if m else page_html))
    return parse_confirmation_text(text)


def parse_confirmation_text(text: str) -> list[Confirmation]:
    out, series = [], None
    for line in text.splitlines():
        if (h := _CONF_HEADER.match(line)):
            series = h.group(1).upper()
            continue
        if series is None:
            continue
        r = _CONF_ROW.match(line)
        if not r:
            continue
        num, mon, day, yy, hms, who, tel, group, images = r.groups()
        if mon not in _MONTHS:
            continue
        h, mi, s = map(int, hms.split(":"))
        post = eastern_to_utc(datetime(2000 + int(yy), _MONTHS[mon], int(day), h, mi, s))
        out.append(Confirmation(series, num, post, who, tel, group, images.split()[0] if images else ""))
    return out
