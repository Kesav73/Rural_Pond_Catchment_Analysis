import asyncio
import base64
import io
import json

import cv2
import httpx
import numpy as np
from PIL import Image

from app.db import postgres

WMS_URL = "https://titiler.terrascope.be/wms"

# Verified live against the endpoint (the layer list is discoverable via an invalid-LAYER error).
# All four of these are required and version-sensitive: the service rejects WMS 1.1.1, rejects a
# missing `styles`, and rejects TIME formats other than a plain ISO date (e.g. "2021" fails).
LAYER = "esa-worldcover-map-10m-2021-v2_map"
WMS_VERSION = "1.3.0"
WMS_TIME = "2021-01-01"

# Second, independent water signal (Tasks.md 3.12). Water absorbs shortwave infrared almost
# completely, so it reads near-black — a *physical measurement* rather than a classifier's
# judgement, which is why it catches ponds too small or too spectrally mixed for WorldCover's
# classifier to commit to a label. Same keyless endpoint, same fetch/cache/decode path.
SWIR_LAYER = "esa-worldcover-swir-10m-2021-v2_swir"

# Measured on the Bhilai bbox (1024x1024): mean SWIR brightness inside WorldCover water 17.4 vs
# 75.2 outside — a ~4x separation. At <40 this recovers 93.1% of WorldCover's own water while
# flagging only 1.15% extra pixels, and that extra is mostly the small ponds the classifier hedged
# on. Higher thresholds smear badly (SWIR<60 flags 17% of the scene), so 40 is the knee.
SWIR_WATER_THRESHOLD = 40

# ESA WorldCover class 80 "Permanent water bodies" in the official palette. Verified present in a
# real response for the Bhilai bbox (5.30% of pixels), alongside built-up/tree/cropland classes
# that also matched the published palette exactly.
WATER_RGB = (0, 100, 200)

# Native resolution is 10 m/px; request roughly that, but keep the image in a sane range.
NATIVE_RESOLUTION_M = 10.0
MIN_IMAGE_PX = 256
MAX_IMAGE_PX = 2048

# A candidate is treated as an existing water body if at least this fraction of its footprint is
# classified permanent water. Not 0 — WorldCover is 10 m data and a candidate merely *touching*
# a river's edge pixel shouldn't be discarded; not high either, since a real existing pond will
# be largely water.
WATER_OVERLAP_THRESHOLD = 0.30

# Proximity buffer for existing water *bodies* (Tasks.md 3.12). You do not dig a pond beside a
# pond, so nearness is disqualifying, not just overlap. This deliberately differs from the 15 m
# buffer that `water_exclusion` applies to waterways: a pond *near a stream* is desirable (that is
# the inflow), so only a pond sitting in the channel is excluded there.
#
# The asymmetry that justifies erring high: a false positive costs one candidate out of ~455; a
# false negative proposes a pond on top of an existing pond and destroys trust in the tool. With
# only 5 outputs needed, losing good sites to catch missed ponds is the right trade.
# 50 m is the one value here flagged as judgement rather than measurement — re-tune on real results.
WATER_BUFFER_M = 50.0

_METRES_PER_DEGREE = 111_320.0


def _image_size(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> tuple[int, int]:
    mid_lat_rad = np.radians((min_lat + max_lat) / 2)
    width_m = (max_lon - min_lon) * _METRES_PER_DEGREE * np.cos(mid_lat_rad)
    height_m = (max_lat - min_lat) * _METRES_PER_DEGREE
    width = int(np.clip(width_m / NATIVE_RESOLUTION_M, MIN_IMAGE_PX, MAX_IMAGE_PX))
    height = int(np.clip(height_m / NATIVE_RESOLUTION_M, MIN_IMAGE_PX, MAX_IMAGE_PX))
    return width, height


async def _fetch_layer_png(
    layer: str, cache_kind: str, min_lon, min_lat, max_lon, max_lat, width: int, height: int
) -> bytes:
    """Fetch one WMS layer as PNG bytes, cached in Postgres. Raises on failure (callers degrade)."""
    cache_key = (
        f"{cache_kind}:{min_lon:.4f}:{min_lat:.4f}:{max_lon:.4f}:{max_lat:.4f}:{width}x{height}"
    )
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.TILE_CACHE} WHERE cache_key = $1", cache_key
        )
    if row is not None:
        encoded = json.loads(row["data"]).get("png_b64")
        if encoded:
            return base64.b64decode(encoded)

    params = {
        "service": "WMS",
        "request": "GetMap",
        "version": WMS_VERSION,
        "layers": layer,
        "styles": "",
        "format": "image/png",
        # WMS 1.3.0 with EPSG:4326 takes bbox as lat/lon (axis order differs from 1.1.1).
        "crs": "EPSG:4326",
        "bbox": f"{min_lat},{min_lon},{max_lat},{max_lon}",
        "width": width,
        "height": height,
        "TIME": WMS_TIME,
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.get(WMS_URL, params=params)
        response.raise_for_status()
        if "image" not in response.headers.get("content-type", ""):
            raise ValueError(f"non-image response: {response.text[:200]}")
        png_bytes = response.content

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {postgres.TILE_CACHE} (cache_key, kind, data)
            VALUES ($1, $2, $3::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            cache_kind,
            json.dumps({"png_b64": base64.b64encode(png_bytes).decode()}),
        )
    return png_bytes


def _buffer_pixels(min_lon, min_lat, max_lon, max_lat, width: int, height: int) -> int:
    """WATER_BUFFER_M expressed in mask pixels, from the mask's own ground scale."""
    mid_lat_rad = np.radians((min_lat + max_lat) / 2)
    metres_per_px_x = (max_lon - min_lon) * _METRES_PER_DEGREE * np.cos(mid_lat_rad) / max(width, 1)
    metres_per_px_y = (max_lat - min_lat) * _METRES_PER_DEGREE / max(height, 1)
    metres_per_px = max(float((metres_per_px_x + metres_per_px_y) / 2), 1e-6)
    return max(1, int(round(WATER_BUFFER_M / metres_per_px)))


async def fetch_water_mask(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float
) -> dict:
    """Fetch a combined existing-water mask for a bbox: WorldCover class 80 OR dark SWIR, dilated
    by WATER_BUFFER_M so nearness to an existing water body is disqualifying, not just overlap.

    Returns {"mask", "bbox", "available", "error", "worldcover_available", "swir_available",
             "buffer_px"}. Soft failure throughout: WorldCover alone, SWIR alone, or neither all
    produce a usable answer — `available` is True if *either* signal came back, and the caller
    reports a degraded screen rather than implying a full one (9.6).

    Note the mask is in EPSG:4326 (linear in lon/lat over the bbox), *not* Web Mercator like the
    elevation grid — `water_overlap_fraction` handles that mapping.
    """
    width, height = _image_size(min_lon, min_lat, max_lon, max_lat)
    box = (min_lon, min_lat, max_lon, max_lat)

    async def _load(layer: str, kind: str):
        try:
            png = await _fetch_layer_png(layer, kind, *box, width, height)
            return np.asarray(Image.open(io.BytesIO(png)).convert("RGB")), None
        except Exception as exc:  # noqa: BLE001 — degrade this signal, never fail the request
            return None, f"{type(exc).__name__}: {exc}"

    # Concurrent: the two layers are independent and each is a full network round trip.
    (cover_rgb, cover_error), (swir_rgb, swir_error) = await asyncio.gather(
        _load(LAYER, "worldcover"), _load(SWIR_LAYER, "worldcover_swir")
    )

    masks = []
    if cover_rgb is not None:
        masks.append(np.all(cover_rgb == np.array(WATER_RGB, dtype=np.uint8), axis=-1))
    if swir_rgb is not None:
        masks.append(swir_rgb.mean(axis=2) < SWIR_WATER_THRESHOLD)

    if not masks:
        return {
            "mask": None, "bbox": box, "available": False,
            "error": "; ".join(e for e in (cover_error, swir_error) if e),
            "worldcover_available": False, "swir_available": False, "buffer_px": 0,
        }

    # Union, not intersection — neither source is authoritative alone (same principle as the
    # OSM/WorldCover union in water_exclusion).
    combined = masks[0]
    for extra in masks[1:]:
        combined = np.logical_or(combined, extra)

    buffer_px = _buffer_pixels(*box, width, height)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * buffer_px + 1, 2 * buffer_px + 1))
    dilated = cv2.dilate(combined.astype(np.uint8), kernel).astype(bool)

    return {
        "mask": dilated,
        "bbox": box,
        "available": True,
        "error": "; ".join(e for e in (cover_error, swir_error) if e) or None,
        "worldcover_available": cover_rgb is not None,
        "swir_available": swir_rgb is not None,
        "buffer_px": buffer_px,
    }


def water_overlap_fraction(ring: list[list[float]], water: dict) -> float:
    """Fraction of a candidate's lon/lat polygon that falls on permanent-water pixels.

    The mask is linear in lon/lat over its bbox, so mapping a lon/lat ring into mask pixels is a
    direct linear scale (no Mercator maths needed here, unlike the elevation grid).
    """
    mask = water.get("mask")
    if mask is None:
        return 0.0
    min_lon, min_lat, max_lon, max_lat = water["bbox"]
    height, width = mask.shape
    if max_lon <= min_lon or max_lat <= min_lat:
        return 0.0

    points = []
    for lon, lat in ring:
        x = (lon - min_lon) / (max_lon - min_lon) * width
        y = (max_lat - lat) / (max_lat - min_lat) * height  # row 0 is the north edge
        points.append([x, y])

    polygon = np.array([points], dtype=np.int32)
    shape_mask = np.zeros(mask.shape, dtype=np.uint8)
    cv2.fillPoly(shape_mask, polygon, 1)
    total = int(shape_mask.sum())
    if total == 0:
        return 0.0
    return float(np.logical_and(shape_mask.astype(bool), mask).sum()) / total
