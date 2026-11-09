"""Alerts for alert-tier objects: a Discord webhook post and/or an ntfy push.

Each channel is configured by an environment variable or a one-line file under
data/state/ (gitignored with the rest of data/); an unconfigured channel is skipped.

  Discord  $KREUTZ_DISCORD_WEBHOOK or discord_webhook.txt: the webhook URL
  ntfy     $KREUTZ_NTFY_URL or ntfy_url.txt: the topic URL, https://ntfy.sh/<topic>.
           A push to the phone app subscribed to that topic; the topic name is the
           only secret, so it is a long random string.

$KREUTZ_DASHBOARD_URL or dashboard_url.txt, when set, is added as the link to open.
Each track id is sent at most once per channel (data/state/<channel>_posted.json).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import net, paths


def _setting(env: str, fname: str) -> str | None:
    v = os.environ.get(env)
    if v and v.strip():
        return v.strip()
    p = paths.STATE_DIR / fname
    return (p.read_text().strip() or None) if p.exists() else None


def webhook_url() -> str | None:
    return _setting("KREUTZ_DISCORD_WEBHOOK", "discord_webhook.txt")


def ntfy_url() -> str | None:
    return _setting("KREUTZ_NTFY_URL", "ntfy_url.txt")


def dashboard_url() -> str | None:
    return _setting("KREUTZ_DASHBOARD_URL", "dashboard_url.txt")


def configured() -> bool:
    return bool(webhook_url() or ntfy_url())


def _posted_file(channel: str = "discord") -> Path:
    return paths.STATE_DIR / f"{channel}_posted.json"


def already_posted(track_id: str, channel: str = "discord") -> bool:
    p = _posted_file(channel)
    return track_id in (json.loads(p.read_text()) if p.exists() else [])


def _mark_posted(track_id: str, channel: str = "discord") -> None:
    p = _posted_file(channel)
    ids = json.loads(p.read_text()) if p.exists() else []
    ids.append(track_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(ids[-500:]))


def alert_text(d: dict, label: str, dashboard: str | None) -> str:
    r = d.get("report")
    if r:  # a report already on the site when we alert is a loss, whatever the following time
        al = r.get("alert_lead_h")
        rep = f"already reported by {r['reporter']}" + (f" ({al:+.1f} h relative to this alert)" if al is not None else "")
    else:
        rep = f"UNREPORTED on {label}" + (f"; a {d['lasco']['camera']} claim by {d['lasco']['reporter']} overlaps in time"
                                          if d.get("lasco") else "")
    lines = [f"**ALERT** {label} `{d['id']}` — {rep}",
             f"{d['n_hits']} hits, best {d['best_sig']}σ, at ({d['x']}, {d['y']}) moving ({d['velocity'][0]}, {d['velocity'][1]}) px/frame"
             f" · first seen {d['first_seen'][5:16].replace('T', ' ')} UTC · last {d['last_seen'][5:16].replace('T', ' ')} UTC"]
    if dashboard:
        lines.append(dashboard)
    return "\n".join(lines)


def _ascii(s: str) -> str:
    """HTTP headers carry the ntfy message: markdown and non-ASCII out."""
    for a, b in (("**", ""), ("`", ""), ("σ", " sigma"), ("—", "-"), ("·", "|"), ("\n", " | ")):
        s = s.replace(a, b)
    return s.encode("ascii", "replace").decode()


NTFY_MAX_ATTACH = 2_000_000  # bytes: ntfy.sh's free attachment limit; a larger sheet goes without its picture


def push_ntfy(title: str, text: str, urgent: bool, click: str | None = None, image: Path | None = None) -> None:
    """One ntfy notification. `urgent` is priority 5 (the app can sound through do-not-disturb), else 4."""
    headers = {"Title": _ascii(title), "Priority": "5" if urgent else "4", "Tags": "comet"}
    if click:
        headers["Click"] = click
    s = net.session()
    if image is not None and image.exists() and image.stat().st_size <= NTFY_MAX_ATTACH:
        resp = s.put(ntfy_url(), data=image.read_bytes(), headers=headers | {"Message": _ascii(text), "Filename": image.name}, timeout=30)
        if resp.ok:
            return
    resp = s.post(ntfy_url(), data=_ascii(text).encode(), headers=headers, timeout=30)
    resp.raise_for_status()


def post_alert(d: dict, label: str, sheet: Path | None, dashboard: str | None = None) -> bool:
    """Send one alert-tier object to every configured channel that has not had it; True if any was sent."""
    sent = False
    dashboard = dashboard or dashboard_url()
    if webhook_url() and not already_posted(d["id"]):
        files = {"file": (sheet.name, sheet.read_bytes(), "image/png")} if sheet and sheet.exists() else None
        resp = net.session().post(webhook_url(), data={"content": alert_text(d, label, dashboard)}, files=files, timeout=30)
        resp.raise_for_status()
        _mark_posted(d["id"])
        sent = True
    if ntfy_url() and not already_posted(d["id"], "ntfy"):
        r = d.get("report")
        title = f"{label} comet alert: " + (f"already reported by {r['reporter']}" if r else "UNREPORTED")
        text = "\n".join(alert_text(d, label, None).split("\n")[1:]) + f"\n{d['id']}"
        push_ntfy(title, text, urgent=not r, click=dashboard, image=sheet)
        _mark_posted(d["id"], "ntfy")
        sent = True
    return sent


def send_test() -> list[str]:
    """A test message on every configured channel; the channels it went to."""
    out = []
    if webhook_url():
        net.session().post(webhook_url(), data={"content": "kreutz test: alerts from the watcher will arrive here."}, timeout=30).raise_for_status()
        out.append("discord")
    if ntfy_url():
        push_ntfy("kreutz test", "Alerts from the comet watcher will arrive here.", urgent=False, click=dashboard_url())
        out.append("ntfy")
    return out
