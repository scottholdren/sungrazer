"""Where things live on disk. Everything under data/ is gitignored and rebuildable."""

import os
from pathlib import Path

ROOT = Path(os.environ.get("KREUTZ_ROOT", Path(__file__).resolve().parents[2]))
DATA = ROOT / "data"
FRAMES = DATA / "frames"
STATE_DIR = DATA / "state"
CACHE = DATA / "cache"
REPORTS_DB = DATA / "reports.sqlite"
# Scorecards, surviving tracks and contact sheets: small, committed for review.
RESULTS = ROOT / "results"


def frame_dir(camera: str, yyyymmdd: str) -> Path:
    return FRAMES / camera / yyyymmdd
