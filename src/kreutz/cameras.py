"""Per-camera data-source configuration.

Verified 2026-10-24:
  * NCEI archive is an S3 bucket behind https://archive.data.noaa.gov/satellite-spaceweather,
    one FITS per frame (no tars) under <prefix>/<level>/YYYY/MM/DD/.
  * SWPC real-time feed lists ~30 days of frames in <live>/fits.json.
  * CCOR-1 (on GOES-19) has L1B at both. CCOR-2 (on SWFO-L1) has only L1A at both,
    so it uses L1A. CCOR-2 L1A headers still say BUNIT = MSB (mean solar
    brightness), like CCOR-1 L1B; plate scale is 24.3"/px vs CCOR-1's 19.3"/px.
"""

from dataclasses import dataclass

NCEI_BASE = "https://archive.data.noaa.gov/satellite-spaceweather"
SWPC_BASE = "https://services.swpc.noaa.gov"


@dataclass(frozen=True)
class Camera:
    id: str  # our id, used in paths and the DB: "ccor1" / "ccor2"
    sungrazer_name: str  # how the Sungrazer site labels it
    ncei_prefix: str
    levels: tuple[str, ...]  # archive levels to try, in preference order
    live_index: str  # SWPC JSON listing of recent frames
    plate_scale: float  # arcsec per full-res pixel (CDELT, 2026-11-02 headers)
    nominal_sun: tuple[float, float]  # Sun centre in report px when no frame is at hand (true centre within ~35 px)


CAMERAS = {
    "ccor1": Camera(
        id="ccor1",
        sungrazer_name="CCOR1",
        ncei_prefix="SWFO/GOES-19/CCOR-1",
        levels=("ccor1-l1b", "ccor1-l1a"),
        live_index=f"{SWPC_BASE}/products/ccor1/fits.json",
        plate_scale=19.33,
        nominal_sun=(1024.0, 960.0),
    ),
    "ccor2": Camera(
        id="ccor2",
        sungrazer_name="CCOR2",
        ncei_prefix="SWFO/SOLAR-1/CCOR-2",
        levels=("ccor2-l1b", "ccor2-l1a"),
        live_index=f"{SWPC_BASE}/products/ccor2/fits.json",
        plate_scale=24.27,
        nominal_sun=(1018.5, 994.0),  # CRPIX (1019.5, 926.0) 1-based, rows flipped for display
    ),
}

BY_SUNGRAZER_NAME = {c.sungrazer_name: c.id for c in CAMERAS.values()}


def px_scale(camera_id: str) -> float:
    """How many of this camera's pixels one CCOR-1 pixel covers: the detector's
    pixel thresholds (glare radius, arm band, speeds) were set on CCOR-1 and are
    scaled by this for the other camera."""
    return CAMERAS["ccor1"].plate_scale / CAMERAS[camera_id].plate_scale


def get(camera_id: str) -> Camera:
    try:
        return CAMERAS[camera_id]
    except KeyError:
        raise SystemExit(f"unknown camera {camera_id!r}; choose from {', '.join(CAMERAS)}")
