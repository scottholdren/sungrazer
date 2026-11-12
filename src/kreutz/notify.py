"""Alerts for alert-tier objects: a Discord webhook post and/or an ntfy push.

Each channel is configured by an environment variable or a one-line file under
data/state/ (gitignored with the rest of data/); an unconfigured channel is skipped.

  Discord  $KREUTZ_DISCORD_WEBHOOK or discord_webhook.txt: the webhook URL
  ntfy     $KREUTZ_NTFY_URL or ntfy_url.txt: the topic URL, https://ntfy.sh/<topic>.
           A push to the phone app subscribed to that topic; the topic name is the
           only secret, so it is a long random string.

Tapping the phone push opens the camera's live-finds folder on GitHub, the follow-up
the package itself. Nothing links the dashboard: it sits on a network the phone cannot
reach.

Only the alert is sent, when a track reaches the alert tier, at most once per track
and channel (data/state/<channel>_posted.json). A "following" event (a track's
third hit) exists but no channel takes it: the owner turned it off (CHANNEL_EVENTS). The picture is
the contact sheet as an animation (`export.write_sheet_gif`). After an alert the
watcher builds and publishes the hunter's package and sends its README link as
a quiet follow-up (the dashboard link only opens on the home network), and
publishes it once more when the track has ended; auto_export.txt holding "off"
stops that.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import net, paths


CHANNEL_EVENTS = {"discord": ("alert",), "ntfy": ("alert",)}  # the owner wants alerts only (2026-11-09)


def _key(track_id: str, event: str) -> str:
    return track_id if event == "alert" else f"{track_id}:{event}"  # alerts keep the bare id of older logs


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


def _facts(d: dict) -> str:
    """One line of what the track is doing: size, brightness and its trend, where, how fast, which way."""
    import math

    sig = [h["sig"] for h in d["hits"]]
    trend = "brightening" if sig[-1] > sig[0] * 1.3 else "fading" if sig[-1] < sig[0] / 1.3 else "steady"
    out = f"{d['n_hits']} hits, best {d['best_sig']}σ, {trend}"
    vx, vy = d["velocity"]
    v = math.hypot(vx, vy)
    if d.get("sun_xy") and v:
        sx, sy = d["sun_xy"]
        r = math.hypot(d["x"] - sx, d["y"] - sy)
        off = math.degrees(math.acos(max(-1.0, min(1.0, (vx * (sx - d["x"]) + vy * (sy - d["y"])) / (r * v + 1e-9)))))
        out += f" · {r:.0f} px from the Sun, {v:.1f} px/frame, heading {off:.0f}° off the Sun"
    else:
        out += f" · at ({d['x']}, {d['y']}), {v:.1f} px/frame"
    return out


POSITIONS = 5  # newest hits listed in an alert, in the Sungrazer form's layout


def alert_text(d: dict, label: str, event: str = "alert", tag: dict | None = None) -> str:
    r = d.get("report")
    if r:  # a report already on the site when we alert is a loss, whatever the following time
        al = r.get("alert_lead_h")
        rep = f"already reported by {r['reporter']}" + ("" if al is None or event != "alert" else
                                                           f" (we alerted {-al:.1f} h before the report)" if al < 0 else
                                                           f" ({al:.1f} h before this alert)")
    else:
        rep = f"UNREPORTED on {label}" + (f"; a {d['lasco']['camera']} claim by {d['lasco']['reporter']} overlaps in time"
                                          if d.get("lasco") else "")
    head = "**ALERT**" if event == "alert" else "following"
    lines = [f"{head} {label} `{d['id']}` — {rep}", _facts(d)]
    if d.get("other"):
        lines.append("not on the Kreutz lane (bright mover in another direction)")
    if d.get("same_as"):  # the dashboard has one card per object, under the camera that saw it first
        sa = d["same_as"]
        lines.append(f"also on {sa['label']} as `{sa['id']}` ({sa['n_hits']} hits, best {sa['best_sig']}σ)")
    if tag and tag.get("label"):
        lines.append(f"you tagged it {tag['label']} {tag.get('at', '')[11:16]} UTC")
    if event == "alert":
        lines += ["```"] + [f"{h['t'][11:13]}{h['t'][14:16]} {h['x']:.0f} {h['y']:.0f}" for h in d["hits"][-POSITIONS:]] + ["```"]
    lines.append("picture, left to right: the raw frame · that frame's map · the last 8 maps stacked along the motion")
    return "\n".join(lines)  # no dashboard link: it only opens on the home network, and the owner asked it out


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


def mark_sent(track_id: str, event: str = "alert") -> None:
    """Record an event as sent on every channel without sending it (the object has had it under another id)."""
    for ch in CHANNEL_EVENTS:
        if event in CHANNEL_EVENTS[ch] and not already_posted(_key(track_id, event), ch):
            _mark_posted(_key(track_id, event), ch)


def _mime(p: Path) -> str:
    return "image/gif" if p.suffix.lower() == ".gif" else "image/png"


def post_alert(d: dict, label: str, image: Path | None, click: str | None = None, event: str = "alert",
               tag: dict | None = None) -> bool:
    """Send one event of one object to every configured channel that takes that event
    and has not had it; True if any was sent. `image` is attached (an animated GIF plays
    in Discord); tapping the phone push opens `click`."""
    sent = False
    key = _key(d["id"], event)
    if webhook_url() and event in CHANNEL_EVENTS["discord"] and not already_posted(key):
        files = {"file": (image.name, image.read_bytes(), _mime(image))} if image and image.exists() else None
        resp = net.session().post(webhook_url(), data={"content": alert_text(d, label, event, tag)}, files=files, timeout=30)
        resp.raise_for_status()
        _mark_posted(key)
        sent = True
    if ntfy_url() and event in CHANNEL_EVENTS["ntfy"] and not already_posted(key, "ntfy"):
        r = d.get("report")
        title = f"{label} comet alert: " + (f"already reported by {r['reporter']}" if r else "UNREPORTED")
        text = f"{_facts(d)}\n{d['id']}"
        push_ntfy(title, text, urgent=not r, click=click, image=image)
        _mark_posted(key, "ntfy")
        sent = True
    return sent


def post_note(text: str, title: str, link: str | None = None) -> None:
    """A follow-up to an alert on every configured channel: quiet on the phone (priority 2,
    no sound), and tapping it opens `link`. On Discord the link is in angle brackets, which
    stops the embed (the repo's card) from unrolling under it."""
    if webhook_url():
        net.session().post(webhook_url(), data={"content": text + (f"\n<{link}>" if link else "")}, timeout=30).raise_for_status()
    if ntfy_url():
        headers = {"Title": _ascii(title), "Priority": "2", "Tags": "page_facing_up"}
        if link:
            headers["Click"] = link
        net.session().post(ntfy_url(), data=_ascii(text).encode(), headers=headers, timeout=30).raise_for_status()


def auto_export() -> bool:
    """Whether an alert also builds and publishes the hunter's package and links it
    (data/state/auto_export.txt holding "off" turns that off)."""
    return (_setting("KREUTZ_AUTO_EXPORT", "auto_export.txt") or "on").lower() != "off"


def send_test() -> list[str]:
    """A test message on every configured channel; the channels it went to."""
    out = []
    if webhook_url():
        net.session().post(webhook_url(), data={"content": "kreutz test: alerts from the watcher will arrive here."}, timeout=30).raise_for_status()
        out.append("discord")
    if ntfy_url():
        push_ntfy("kreutz test", "Alerts from the comet watcher will arrive here.", urgent=False)
        out.append("ntfy")
    return out
