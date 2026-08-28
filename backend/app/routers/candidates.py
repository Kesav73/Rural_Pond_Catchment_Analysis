import json

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.db import postgres
from app.services import contours as contours_service
from app.services import elevation as elevation_service
from app.services import overpass as overpass_service
from app.services import terrain as terrain_service
from app.services import water_exclusion as water_exclusion_service
from app.services import worldcover as worldcover_service

router = APIRouter(prefix="/api", tags=["candidates"])

# Bump whenever the detection/ranking/exclusion logic changes, so cached results computed by the
# old logic aren't served forever. Without this, fixing a ranking bug silently has no effect on
# any bbox already in the cache (hit exactly that during verification of the bbox-clipping fix).
CACHE_VERSION = 2


def _parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    try:
        min_lon, min_lat, max_lon, max_lat = (float(value) for value in bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be 'minLon,minLat,maxLon,maxLat'")
    return min_lon, min_lat, max_lon, max_lat


def _to_feature(candidate: dict) -> dict:
    properties = {key: value for key, value in candidate.items() if key != "geometry"}
    return {"type": "Feature", "geometry": candidate["geometry"], "properties": properties}


@router.get("/candidates")
async def get_candidates(
    bbox: str,
    min_depth: float = terrain_service.DEFAULT_MIN_DEPTH_M,
    min_area: float = terrain_service.DEFAULT_MIN_AREA_M2,
    top_n: int = terrain_service.DEFAULT_TOP_N,
):
    """Auto-detect and rank pond-site candidates for a bbox.

    Fully automatic (no user-drawn polygon — see the Phase 3 plan change in Tasks.md): detect
    every depression, score by storage volume x shape, drop stream corridors and existing water
    bodies, return the top N.
    """
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    zoom = settings.default_elevation_zoom
    cache_key = (
        f"candidates:v{CACHE_VERSION}:{min_lon:.5f}:{min_lat:.5f}:{max_lon:.5f}:{max_lat:.5f}"
        f":{zoom}:{min_depth}:{min_area}:{top_n}"
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

    # Candidate detection uses its own lighter smoothing than the contour layer — see
    # terrain.CANDIDATE_SMOOTHING_SIGMA for why sharing the contour value over-smooths.
    smoothed = contours_service.smooth(grid, sigma=terrain_service.CANDIDATE_SMOOTHING_SIGMA)
    filled = terrain_service.priority_flood_fill(smoothed)
    depth, labels, num_zones = terrain_service.label_depressions(
        smoothed, filled, min_depth=min_depth
    )
    zones = terrain_service.extract_zone_properties(
        depth, labels, num_zones, xmin_tile, ymin_tile, zoom, min_area_m2=min_area
    )

    # The elevation grid is tile-aligned, so it overhangs the requested bbox by up to a full tile
    # (~1.8 km at z14). Without clipping, "top 5" could include sites outside the user's view —
    # observed live: ranks 2, 4 and 5 landed off-screen, so the map showed only #1 and #3 and
    # looked broken. Clip on centroid so a returned rank is always something you can actually see.
    zones = [
        zone
        for zone in zones
        if min_lon <= zone["centroid"]["lon"] <= max_lon
        and min_lat <= zone["centroid"]["lat"] <= max_lat
    ]

    ranked = terrain_service.score_and_rank(zones)

    # Both water sources are optional by design: either being down degrades the check rather
    # than failing the request (the other source still applies).
    overpass_result = await overpass_service.fetch_buildings_and_water(
        min_lon, min_lat, max_lon, max_lat
    )
    worldcover_result = await worldcover_service.fetch_water_mask(
        min_lon, min_lat, max_lon, max_lat
    )
    water_index = water_exclusion_service.build_water_index(overpass_result)
    annotated = water_exclusion_service.annotate_water_exclusion(
        ranked, water_index, worldcover_result
    )

    survivors = [candidate for candidate in annotated if not candidate["excluded"]]
    excluded = [candidate for candidate in annotated if candidate["excluded"]]
    selected = survivors[:top_n]

    result = {
        "type": "FeatureCollection",
        "features": [_to_feature(candidate) for candidate in selected],
        "summary": {
            "zones_detected": num_zones,
            "zones_in_view": len(zones),
            "excluded_shape": sum(
                1 for c in excluded if "compactness" in (c["exclusion_reason"] or "")
            ),
            "excluded_water": sum(
                1 for c in excluded if "water body" in (c["exclusion_reason"] or "")
            ),
            "eligible": len(survivors),
            "returned": len(selected),
            "top_n": top_n,
            # Surfaced so the UI can be honest about a degraded check rather than implying
            # every returned site was fully water-screened (Tasks.md 9.6).
            "overpass_available": overpass_result["available"],
            "worldcover_available": worldcover_result["available"],
        },
        # Kept for inspection/debugging: why something was dropped is as useful as what survived.
        "excluded": [
            {
                "candidate_id": c["candidate_id"],
                "area_ha": c["area_ha"],
                "compactness": c["compactness"],
                "reason": c["exclusion_reason"],
            }
            for c in excluded
        ],
    }

    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {postgres.TILE_CACHE} (cache_key, kind, data)
            VALUES ($1, 'candidates', $2::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            json.dumps(result),
        )
    return result


@router.get("/buildings")
async def get_buildings(bbox: str):
    """Building footprints for a bbox — a non-blocking warning layer, never a hard filter."""
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    result = await overpass_service.fetch_buildings_and_water(
        min_lon, min_lat, max_lon, max_lat
    )
    features = []
    for building in result["buildings"]:
        coords = building["coords"]
        if not building["closed"] or len(coords) < 4:
            continue
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [coords]},
                "properties": {"tags": building["tags"]},
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "available": result["available"],
        "error": result["error"],
    }
