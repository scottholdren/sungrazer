"""HTTP helpers shared by every downloader.

Two rules from the project brief live here so nothing can bypass them:
  * every request carries a descriptive User-Agent;
  * "polling" (asking a server "anything new?") happens at most every 15 minutes
    per endpoint. Bulk archive downloads are not polling, but we still pace them.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from . import paths

USER_AGENT = (
    "kreutz-detector/0.1 (amateur Kreutz sungrazer hunting for the NRL Sungrazer "
    "Project; https://github.com/scottholdren/sungrazer)"
)

POLL_INTERVAL_S = 15 * 60
# Gap between consecutive requests to the same host during bulk downloads.
REQUEST_GAP_S = 0.5

_session: requests.Session | None = None
_last_request_at: dict[str, float] = {}


def session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers["User-Agent"] = USER_AGENT
    return _session


def _pace(url: str) -> None:
    host = requests.utils.urlparse(url).netloc
    wait = _last_request_at.get(host, 0) + REQUEST_GAP_S - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def get(url: str, *, params=None, timeout: float = 60, retries: int = 3) -> requests.Response:
    """GET with pacing and a few retries on transient errors (5xx / network)."""
    for attempt in range(retries):
        _pace(url)
        try:
            r = session().get(url, params=params, timeout=timeout)
        except requests.RequestException:
            if attempt == retries - 1:
                raise
        else:
            if r.status_code < 500 or attempt == retries - 1:
                r.raise_for_status()
                return r
        time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def download(url: str, dest: Path, expected_size: int | None = None) -> bool:
    """Download url to dest atomically. Returns False if dest already existed.

    A partially written file is kept as dest + ".part" and never mistaken for a
    finished one, so an interrupted run can simply be restarted.
    """
    if dest.exists() and (expected_size is None or dest.stat().st_size == expected_size):
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    for attempt in range(3):
        _pace(url)
        try:
            with session().get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
            if expected_size is not None and tmp.stat().st_size != expected_size:
                raise OSError(f"size mismatch for {url}")
            os.replace(tmp, dest)
            return True
        except (requests.RequestException, OSError):
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def poll_allowed(name: str, force: bool = False) -> tuple[bool, float]:
    """Check (and record) the 15-minute poll guard for a named endpoint.

    Returns (allowed, seconds_until_allowed).
    """
    state_file = paths.STATE_DIR / "polls.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}
    now = datetime.now(timezone.utc).timestamp()
    remaining = state.get(name, 0) + POLL_INTERVAL_S - now
    if remaining > 0 and not force:
        return False, remaining
    state[name] = now
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state, indent=1))
    return True, 0.0
