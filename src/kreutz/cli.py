"""Command-line entry point: python -m kreutz <command> ..."""

from __future__ import annotations

import argparse
from datetime import date


def _date(s: str) -> date:
    return date.fromisoformat(s)


def cmd_fetch(args) -> None:
    from . import fetch

    if args.live:
        c = fetch.fetch_live(args.camera, hours=args.hours, force=args.force)
    else:
        if not (args.start and args.end):
            raise SystemExit("fetch needs --start and --end, or --live")
        c = fetch.fetch_archive(args.camera, args.start, args.end)
    total = len(fetch.local_frames(args.camera, args.start, args.end))
    print(f"{args.camera}: {c['downloaded']} downloaded, {c['already_had']} already on disk, "
          f"{total} frames on disk in range")


def cmd_inspect(args) -> None:
    from . import inspect

    inspect.inspect(args.fits, png=args.png)


def cmd_reports_sync(args) -> None:
    from .reports import sync

    sync.sync(args.camera, since=args.since, force=args.force)


def cmd_reports_list(args) -> None:
    from .reports import db

    db.print_table(db.list_reports(args.camera, args.start, args.end))


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="kreutz", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download coronagraph frames")
    f.add_argument("--camera", default="ccor1", choices=["ccor1", "ccor2"])
    f.add_argument("--start", type=_date)
    f.add_argument("--end", type=_date)
    f.add_argument("--live", action="store_true", help="pull new frames from the SWPC real-time feed")
    f.add_argument("--hours", type=float, default=6, help="with --live: how far back to fill (default 6)")
    f.add_argument("--force", action="store_true", help="ignore the 15-minute live poll guard")
    f.set_defaults(func=cmd_fetch)

    i = sub.add_parser("inspect", help="describe a FITS frame and save a PNG preview")
    i.add_argument("fits")
    i.add_argument("--png", help="output PNG path (default: next to the FITS file)")
    i.set_defaults(func=cmd_inspect)

    r = sub.add_parser("reports", help="Sungrazer report answer key")
    rsub = r.add_subparsers(dest="rcmd", required=True)
    rs = rsub.add_parser("sync", help="scrape Sungrazer reports into data/reports.sqlite")
    rs.add_argument("--camera", nargs="+", default=["ccor1", "ccor2"], choices=["ccor1", "ccor2"])
    rs.add_argument("--since", type=_date, default=date(2025, 1, 1))
    rs.add_argument("--force", action="store_true", help="ignore the 15-minute poll guard")
    rs.set_defaults(func=cmd_reports_sync)
    rl = rsub.add_parser("list", help="table of reports in a window")
    rl.add_argument("--camera", choices=["ccor1", "ccor2"])
    rl.add_argument("--start", type=_date)
    rl.add_argument("--end", type=_date)
    rl.set_defaults(func=cmd_reports_list)

    args = p.parse_args(argv)
    args.func(args)
