"""Discord webhook posts for alert-tier objects.

The webhook URL is read from $KREUTZ_DISCORD_WEBHOOK or data/state/discord_webhook.txt
(gitignored with the rest of data/). Without one, nothing is posted. Each track id
is posted at most once (data/state/discord_posted.json).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import net, paths


def webhook_url() -> str | None:
    url = os.environ.get("KREUTZ_DISCORD_WEBHOOK")
    if url:
        return url.strip()
    p = paths.STATE_DIR / "discord_webhook.txt"
    return p.read_text().strip() if p.exists() else None


def _posted_file() -> Path:
    return paths.STATE_DIR / "discord_posted.json"


def already_posted(track_id: str) -> bool:
    p = _posted_file()
    return track_id in (json.loads(p.read_text()) if p.exists() else [])


def _mark_posted(track_id: str) -> None:
    p = _posted_file()
    ids = json.loads(p.read_text()) if p.exists() else []
    ids.append(track_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ids[-500:]))


def alert_text(d: dict, label: str, dashboard: str | None) -> str:
    r = d.get("report")
    rep = (f"already reported by {r['reporter']} ({r['lead_h']:+.1f} h)" if r
           else f"UNREPORTED on {label}" + (f"; a {d['lasco']['camera']} claim by {d['lasco']['reporter']} overlaps in time"
                                             if d.get("lasco") else ""))
    lines = [f"**ALERT** {label} `{d['id']}` — {rep}",
             f"{d['n_hits']} hits, best {d['best_sig']}σ, at ({d['x']}, {d['y']}) moving ({d['velocity'][0]}, {d['velocity'][1]}) px/frame"
             f" · first seen {d['first_seen'][5:16].replace('T', ' ')} UTC · last {d['last_seen'][5:16].replace('T', ' ')} UTC"]
    if dashboard:
        lines.append(dashboard)
    return "\n".join(lines)


def post_alert(d: dict, label: str, sheet: Path | None, dashboard: str | None = None) -> bool:
    """Post one alert-tier object; returns True if posted."""
    url = webhook_url()
    if not url or already_posted(d["id"]):
        return False
    files = {"file": (sheet.name, sheet.read_bytes(), "image/png")} if sheet and sheet.exists() else None
    resp = net.session().post(url, data={"content": alert_text(d, label, dashboard)}, files=files, timeout=30)
    resp.raise_for_status()
    _mark_posted(d["id"])
    return True
