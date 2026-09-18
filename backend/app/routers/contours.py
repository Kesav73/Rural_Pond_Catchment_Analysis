import json

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.db import postgres
from app.services import contours as contours_service
from app.services import elevation as elevation_service
from app.services import gridref as gridref_service

router = APIRouter(prefix="/api", tags=["contours"])


@router.get("/contours")
async def get_contours(bbox: str, interval: float = 2.0, style: str = "bands"):
    """Contours for a bbox: filled elevation `bands` (default) or iso-`lines` clipped to the bbox."""
    if style not in ("bands", "lines"):
        raise HTTPException(status_code=400, detail="style must be 'bands' or 'lines'")
    if interval <= 0:
        raise HTTPException(status_code=400, detail="interval must be positive")
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in bbox.split(","))
    except ValueError:
        raise HTTPException(
            status_code=400, detail="bbox must be 'minLon,minLat,maxLon,maxLat'"
        )

    zoom = settings.default_elevation_zoom
    cache_key = (
        f"contours:{min_lon:.5f}:{min_lat:.5f}:{max_lon:.5f}:{max_lat:.5f}:{zoom}:{interval}"
    ) + (
        # Both styles are clipped to the bbox. Bands gained clipping later than lines, so they use a
        # new key rather than the old one, which would serve unclipped bands from the cache.
        ":bands-clip" if style == "bands" else ":lines"
    )

    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.TILE_CACHE} WHERE cache_key = $1", cache_key
        )
        if row is not None:
            return json.loads(row["data"])

    try:
        grid, xmin_tile, ymin_tile, zoom = await elevation_service.get_elevation_grid(
            min_lon, min_lat, max_lon, max_lat, zoom
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    smoothed = contours_service.smooth(grid)
    gridref = gridref_service.TileGridRef(xmin_tile, ymin_tile, zoom, grid.shape)
    if style == "lines":
        result = contours_service.extract_contour_lines(
            smoothed, gridref, interval, clip_bbox=(min_lon, min_lat, max_lon, max_lat)
        )
    else:
        result = contours_service.extract_contour_bands(
            smoothed, gridref, interval, clip_bbox=(min_lon, min_lat, max_lon, max_lat)
        )

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {postgres.TILE_CACHE} (cache_key, kind, data)
            VALUES ($1, 'contours', $2::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            json.dumps(result),
        )

    return result
