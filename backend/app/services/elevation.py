import asyncio
import base64
import io
import json
import math

import httpx
import numpy as np
from PIL import Image

from app.db import postgres

TERRAIN_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
TILE_SIZE = 256

# AWS Terrarium tiles 404 at z16+; z15 (~4.45 m/px at Chhattisgarh's latitude) is the finest available.
MAX_TILE_ZOOM = 15

# Safety cap so a request over an unbounded area (e.g. a whole district) can't trigger hundreds
# of tile fetches. 12x12 keeps worst-case latency reasonable; callers should zoom in past this.
MAX_TILES_PER_AXIS = 12


def deg2tile(lat_deg: float, lon_deg: float, zoom: int) -> tuple[float, float]:
    lat_rad = math.radians(lat_deg)
    n = 2.0**zoom
    xtile = (lon_deg + 180.0) / 360.0 * n
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def tile2deg(xtile: float, ytile: float, zoom: int) -> tuple[float, float]:
    n = 2.0**zoom
    lon_deg = xtile / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * ytile / n)))
    lat_deg = math.degrees(lat_rad)
    return lat_deg, lon_deg


def pixel_to_lonlat(
    col: float, row: float, xmin_tile: int, ymin_tile: int, zoom: int
) -> list[float]:
    """Grid pixel (col, row) -> [lon, lat]. Fractional tile coords keep Mercator's
    non-linearity correct, so this is exact rather than a linear bbox interpolation."""
    xtile = xmin_tile + col / TILE_SIZE
    ytile = ymin_tile + row / TILE_SIZE
    lat, lon = tile2deg(xtile, ytile, zoom)
    return [lon, lat]


def lonlat_to_pixel(
    lon: float, lat: float, xmin_tile: int, ymin_tile: int, zoom: int
) -> tuple[float, float]:
    """Inverse of pixel_to_lonlat: [lon, lat] -> (col, row) in the stitched grid."""
    xtile, ytile = deg2tile(lat, lon, zoom)
    return (xtile - xmin_tile) * TILE_SIZE, (ytile - ymin_tile) * TILE_SIZE


def ground_resolution(zoom: int, lat: float) -> float:
    """Metres per pixel for Web Mercator at this zoom and latitude (256px tiles).
    Verified reference point: 8.9 m/px at z14, ~21 N (per Verification_Results.md)."""
    return 156543.03392804097 * math.cos(math.radians(lat)) / (2**zoom)


def tile_range_for_bbox(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int
) -> tuple[int, int, int, int]:
    x1, y1 = deg2tile(max_lat, min_lon, zoom)  # NW corner (y grows southward)
    x2, y2 = deg2tile(min_lat, max_lon, zoom)  # SE corner
    xmin, xmax = int(math.floor(min(x1, x2))), int(math.floor(max(x1, x2)))
    ymin, ymax = int(math.floor(min(y1, y2))), int(math.floor(max(y1, y2)))
    return xmin, xmax, ymin, ymax


async def _fetch_tile_bytes(client: httpx.AsyncClient, z: int, x: int, y: int) -> bytes | None:
    resp = await client.get(TERRAIN_TILE_URL.format(z=z, x=x, y=y))
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.content


async def _get_tile_png(z: int, x: int, y: int, client: httpx.AsyncClient) -> bytes | None:
    cache_key = f"terrain:{z}:{x}:{y}"
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.TILE_CACHE} WHERE cache_key = $1", cache_key
        )
        if row is not None:
            encoded = json.loads(row["data"]).get("png_b64")
            return base64.b64decode(encoded) if encoded else None

        png_bytes = await _fetch_tile_bytes(client, z, x, y)
        payload = {"png_b64": base64.b64encode(png_bytes).decode() if png_bytes else None}
        await conn.execute(
            f"""
            INSERT INTO {postgres.TILE_CACHE} (cache_key, kind, data)
            VALUES ($1, 'elevation_tile', $2::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            json.dumps(payload),
        )
        return png_bytes


def _decode_terrarium(png_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    arr = np.asarray(img, dtype=np.float64)
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    return r * 256 + g + b / 256 - 32768


async def get_elevation_grid(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int
) -> tuple[np.ndarray, int, int, int]:
    """Fetch + stitch the tile grid covering bbox at zoom. Returns (grid, xmin_tile, ymin_tile, zoom)."""
    zoom = min(zoom, MAX_TILE_ZOOM)
    xmin, xmax, ymin, ymax = tile_range_for_bbox(min_lon, min_lat, max_lon, max_lat, zoom)
    n_x, n_y = xmax - xmin + 1, ymax - ymin + 1
    if n_x > MAX_TILES_PER_AXIS or n_y > MAX_TILES_PER_AXIS:
        raise ValueError(
            f"Requested area is too large at zoom {zoom} ({n_x}x{n_y} tiles, max "
            f"{MAX_TILES_PER_AXIS}x{MAX_TILES_PER_AXIS}) — zoom in further before requesting contours."
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        sem = asyncio.Semaphore(8)

        async def fetch_one(x: int, y: int):
            async with sem:
                return x, y, await _get_tile_png(zoom, x, y, client)

        tasks = [fetch_one(x, y) for y in range(ymin, ymax + 1) for x in range(xmin, xmax + 1)]
        results = await asyncio.gather(*tasks)

    grid = np.zeros((n_y * TILE_SIZE, n_x * TILE_SIZE), dtype=np.float64)
    for x, y, png_bytes in results:
        if png_bytes is None:
            # No tile at this z/x/y (out of coverage) — leave as 0m, doesn't happen over land.
            continue
        row_off = (y - ymin) * TILE_SIZE
        col_off = (x - xmin) * TILE_SIZE
        grid[row_off : row_off + TILE_SIZE, col_off : col_off + TILE_SIZE] = _decode_terrarium(
            png_bytes
        )

    return grid, xmin, ymin, zoom
