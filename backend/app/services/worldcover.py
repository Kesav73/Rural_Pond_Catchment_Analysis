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

_METRES_PER_DEGREE = 111_320.0


def _image_size(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> tuple[int, int]:
    mid_lat_rad = np.radians((min_lat + max_lat) / 2)
    width_m = (max_lon - min_lon) * _METRES_PER_DEGREE * np.cos(mid_lat_rad)
    height_m = (max_lat - min_lat) * _METRES_PER_DEGREE
    width = int(np.clip(width_m / NATIVE_RESOLUTION_M, MIN_IMAGE_PX, MAX_IMAGE_PX))
    height = int(np.clip(height_m / NATIVE_RESOLUTION_M, MIN_IMAGE_PX, MAX_IMAGE_PX))
    return width, height


async def fetch_water_mask(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float
) -> dict:
    """Fetch ESA WorldCover for a bbox and return a boolean permanent-water mask.

    Returns {"mask": np.ndarray|None, "bbox": (...), "available": bool, "error": str|None}.
    Soft failure, same contract as the Overpass layer: a failure here drops the WorldCover half
    of the water check rather than failing the whole candidate request.

    Note the mask is in EPSG:4326 (linear in lon/lat over the bbox), *not* Web Mercator like the
    elevation grid — `water_overlap_fraction` handles that mapping.
    """
    width, height = _image_size(min_lon, min_lat, max_lon, max_lat)
    cache_key = (
        f"worldcover:{min_lon:.4f}:{min_lat:.4f}:{max_lon:.4f}:{max_lat:.4f}:{width}x{height}"
    )

    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.TILE_CACHE} WHERE cache_key = $1", cache_key
        )
    png_bytes = None
    if row is not None:
        encoded = json.loads(row["data"]).get("png_b64")
        if encoded:
            png_bytes = base64.b64decode(encoded)

    if png_bytes is None:
        params = {
            "service": "WMS",
            "request": "GetMap",
            "version": WMS_VERSION,
            "layers": LAYER,
            "styles": "",
            "format": "image/png",
            # WMS 1.3.0 with EPSG:4326 takes bbox as lat/lon (axis order differs from 1.1.1).
            "crs": "EPSG:4326",
            "bbox": f"{min_lat},{min_lon},{max_lat},{max_lon}",
            "width": width,
            "height": height,
            "TIME": WMS_TIME,
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.get(WMS_URL, params=params)
                response.raise_for_status()
                if "image" not in response.headers.get("content-type", ""):
                    raise ValueError(f"non-image response: {response.text[:200]}")
                png_bytes = response.content
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the request
            return {
                "mask": None,
                "bbox": (min_lon, min_lat, max_lon, max_lat),
                "available": False,
                "error": f"{type(exc).__name__}: {exc}",
            }

        async with pool.acquire() as conn:
            await conn.execute(
                f"""
                INSERT INTO {postgres.TILE_CACHE} (cache_key, kind, data)
                VALUES ($1, 'worldcover', $2::jsonb)
                ON CONFLICT (cache_key) DO NOTHING
                """,
                cache_key,
                json.dumps({"png_b64": base64.b64encode(png_bytes).decode()}),
            )

    rgb = np.asarray(Image.open(io.BytesIO(png_bytes)).convert("RGB"))
    mask = np.all(rgb == np.array(WATER_RGB, dtype=np.uint8), axis=-1)
    return {
        "mask": mask,
        "bbox": (min_lon, min_lat, max_lon, max_lat),
        "available": True,
        "error": None,
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
