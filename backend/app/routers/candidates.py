import asyncio
import json

from fastapi import APIRouter, HTTPException

from app.core.config import settings
from app.db import postgres
from app.services import contours as contours_service
from app.services import elevation as elevation_service
from app.services import flow_cache
from app.services import gridref as gridref_service
from app.services import overpass as overpass_service
from app.services import pond_sizing
from app.services import rainfall as rainfall_service
from app.services import terrain as terrain_service
from app.services import water_exclusion as water_exclusion_service
from app.services import worldcover as worldcover_service

router = APIRouter(prefix="/api", tags=["candidates"])

# Bump whenever the detection/ranking/exclusion logic changes, so cached results computed by the
# old logic aren't served forever. Without this, fixing a ranking bug silently has no effect on
# any bbox already in the cache (hit exactly that during verification of the bbox-clipping fix).
#   v2 -> v3: 2026-08-29 pipeline reorder — min_depth 0.3->1.5 m (3.10), water exclusion moved
#             before ranking with SWIR + 50 m buffer (3.12), ranking now driven by catchment-fed
#             runoff volume rather than volume x compactness (4.9 / 6.2 / 6.4).
CACHE_VERSION = 3


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
    rank_mode: str = "sufficiency",
):
    """Auto-detect and rank pond-site candidates for a bbox.

    Fully automatic (no user-drawn polygon — see the Phase 3 plan changes in Tasks.md). Pipeline,
    in the order the 2026-08-29 reorder fixed:

        1. DEM -> smooth -> Priority-Flood (plain) -> depth
        2. depth > min_depth, 8-connected -> zones ; size filter ; clip to viewport
        3. EXCLUDE existing water FIRST (WorldCover + SWIR + 50 m buffer, OSM when reachable)
        4. Priority-Flood (epsilon) -> D8 -> flow accumulation
        5. per zone: catchment area = accumulation[mask].max()   (one .max(), no per-zone fill)
        6. Rational Method runoff -> rank -> top N

    Excluding water *before* ranking is what makes catchment-driven ranking safe: mapped channels
    are gone from the pool before anything is ranked, so "more water draining here" cannot promote
    a river.
    """
    min_lon, min_lat, max_lon, max_lat = _parse_bbox(bbox)
    if rank_mode not in ("sufficiency", "water"):
        raise HTTPException(status_code=400, detail="rank_mode must be 'sufficiency' or 'water'")
    zoom = settings.default_elevation_zoom
    cache_key = (
        f"candidates:v{CACHE_VERSION}:{min_lon:.5f}:{min_lat:.5f}:{max_lon:.5f}:{max_lat:.5f}"
        f":{zoom}:{min_depth}:{min_area}:{top_n}:{rank_mode}"
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

    center_lat = (min_lat + max_lat) / 2
    # One georeference object describes this grid; the terrain engine needs nothing else about
    # where it came from (Tasks_Phase2.md 2.1).
    gridref = gridref_service.TileGridRef(xmin_tile, ymin_tile, zoom, grid.shape)
    resolution_m = gridref.resolution_m
    cell_area_m2 = resolution_m**2

    # The external fetches are independent of the terrain maths and of each other, so start them
    # now and let them run while the CPU work happens. Overpass in particular costs ~18s when every
    # mirror is down; awaiting it serially used to add that straight onto the request.
    water_task = asyncio.create_task(
        worldcover_service.fetch_water_mask(min_lon, min_lat, max_lon, max_lat)
    )
    overpass_task = asyncio.create_task(
        overpass_service.fetch_buildings_and_water(min_lon, min_lat, max_lon, max_lat)
    )
    rainfall_task = asyncio.create_task(
        rainfall_service.fetch_rainfall(center_lat, (min_lon + max_lon) / 2)
    )

    # Candidate detection uses its own lighter smoothing than the contour layer — see
    # terrain.CANDIDATE_SMOOTHING_SIGMA for why sharing the contour value over-smooths.
    smoothed = contours_service.smooth(grid, sigma=terrain_service.CANDIDATE_SMOOTHING_SIGMA)
    filled = terrain_service.priority_flood_fill(smoothed)
    depth, labels, num_zones = terrain_service.label_depressions(
        smoothed, filled, min_depth=min_depth
    )
    zones = terrain_service.extract_zone_properties(
        depth, labels, num_zones, gridref, min_area_m2=min_area
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

    # --- water exclusion, BEFORE any ranking (3.12) ---------------------------------------------
    worldcover_result = await water_task
    overpass_result = await overpass_task
    water_index = water_exclusion_service.build_water_index(overpass_result)
    zones = water_exclusion_service.annotate_water_exclusion(
        zones, water_index, worldcover_result
    )
    excluded_water_count = sum(1 for zone in zones if zone.get("excluded"))

    # --- flow solve, once, for catchment-driven ranking (4.9) -----------------------------------
    accumulation, direction = flow_cache.get_flow_solution(
        cache_key=flow_cache.make_key(min_lon, min_lat, max_lon, max_lat, zoom),
        smoothed=smoothed,
    )
    zones = terrain_service.attach_catchment_metrics(zones, labels, accumulation, cell_area_m2)

    # --- Rational Method + ranking (6.2 / 6.4) --------------------------------------------------
    rainfall = await rainfall_task
    design_storm_mm = (
        rainfall.get("max_single_day_mm") or 0.0 if rainfall.get("available") else 0.0
    )
    ranked = terrain_service.score_and_rank_by_water(
        zones, rainfall_mm=design_storm_mm, mode=rank_mode
    )

    survivors = [candidate for candidate in ranked if not candidate["excluded"]]
    excluded = [candidate for candidate in ranked if candidate["excluded"]]
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
            "excluded_water": excluded_water_count,
            "eligible": len(survivors),
            "returned": len(selected),
            "top_n": top_n,
            "rank_mode": rank_mode,
            "min_depth_m": min_depth,
            "resolution_m": resolution_m,
            "design_storm_mm": design_storm_mm,
            # Surfaced so the UI can be honest about a degraded check rather than implying
            # every returned site was fully water-screened (Tasks.md 9.6).
            "overpass_available": overpass_result["available"],
            "worldcover_available": worldcover_result.get("worldcover_available", False),
            "swir_available": worldcover_result.get("swir_available", False),
            "rainfall_available": bool(rainfall.get("available")),
            "water_buffer_m": worldcover_service.WATER_BUFFER_M,
        },
        # Every constant a user-facing number depends on, with whether it is cited or judgement —
        # so a placeholder is never presented as authoritative (Tasks.md 6.1 / 9.3).
        "assumptions": pond_sizing.constants_provenance(),
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

    # Only cache a result whose water screen actually ran. A transient WMS failure degrades the
    # screen, and caching that would pin the degraded answer in place for this bbox forever —
    # exactly the failure `overpass.py` already guards against by caching successes only. Observed
    # live: one SWIR fetch timed out, the result cached with swir_available=False, and every later
    # request for that view served the weaker screen even though SWIR was healthy again.
    #
    # OSM is excluded from this condition deliberately: it is a bonus source and is currently down
    # for every mirror, so requiring it would disable caching entirely.
    water_screen_complete = (
        worldcover_result.get("worldcover_available", False)
        and worldcover_result.get("swir_available", False)
        and bool(rainfall.get("available"))
    )
    if water_screen_complete:
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
