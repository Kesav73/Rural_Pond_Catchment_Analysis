import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import contours as contours_service
from app.services import elevation as elevation_service
from app.services import rainfall as rainfall_service
from app.services import terrain as terrain_service

router = APIRouter(prefix="/api", tags=["catchment"])

# Above this catchment:pond area ratio the site is effectively an in-stream structure (check-dam
# territory) rather than a farm pond, and the Rational Method will produce a runoff volume far
# beyond anything the pond could hold. Measured ratios ranged 2.6x-145x across both flat urban
# and higher-relief rural test areas, so this is flagged rather than silently trusted — see
# Tasks.md 4.7 / 9.5.
CATCHMENT_RATIO_WARN = 50.0

# A pour point should sit well inside the pond footprint; when little of the polygon drains to its
# own pour point, the delineation is suspect (recorded good case was 94% self-overlap).
SELF_OVERLAP_WARN_PCT = 50.0


class CatchmentRequest(BaseModel):
    bbox: str = Field(..., description="minLon,minLat,maxLon,maxLat")
    polygons: list[dict] = Field(..., description="GeoJSON Polygon geometries")


@router.post("/catchment")
async def compute_catchment(request: CatchmentRequest):
    """Delineate the catchment draining to each candidate pond.

    Takes a list rather than a single polygon because the Phase 3 plan change made site selection
    automatic: the expensive part (epsilon fill + D8 + flow accumulation, ~5s) depends only on the
    bbox, so computing it once and reusing it for all top-N candidates is far cheaper than one
    request per candidate.
    """
    try:
        min_lon, min_lat, max_lon, max_lat = (float(v) for v in request.bbox.split(","))
    except ValueError:
        raise HTTPException(status_code=400, detail="bbox must be 'minLon,minLat,maxLon,maxLat'")
    if not request.polygons:
        raise HTTPException(status_code=400, detail="at least one polygon is required")

    try:
        grid, xmin_tile, ymin_tile, zoom = await elevation_service.get_elevation_grid(
            min_lon, min_lat, max_lon, max_lat, settings.default_elevation_zoom
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Same smoothing as candidate detection so the pond polygons line up with the surface the
    # flow network was derived from.
    smoothed = contours_service.smooth(grid, sigma=terrain_service.CANDIDATE_SMOOTHING_SIGMA)
    filled = terrain_service.priority_flood_fill_epsilon(smoothed)
    direction = terrain_service.d8_flow_direction(filled)
    accumulation = terrain_service.flow_accumulation(direction, filled)

    center_lat = (min_lat + max_lat) / 2
    resolution_m = elevation_service.ground_resolution(zoom, center_lat)
    cell_area_ha = resolution_m**2 / 10_000

    results = []
    for index, geometry in enumerate(request.polygons):
        try:
            ring = geometry["coordinates"][0]
        except (KeyError, IndexError, TypeError):
            raise HTTPException(status_code=400, detail=f"polygon {index} is not a GeoJSON Polygon")

        polygon_mask = terrain_service.polygon_to_mask(
            ring, grid.shape, xmin_tile, ymin_tile, zoom
        )
        pond_cells = int(polygon_mask.sum())
        if pond_cells == 0:
            results.append(
                {
                    "index": index,
                    "error": "polygon falls outside the elevation grid or is smaller than one cell",
                }
            )
            continue

        pour = terrain_service.select_pour_point(accumulation, polygon_mask)
        catchment_mask = terrain_service.delineate_catchment(direction, *pour)

        catchment_ha = float(catchment_mask.sum() * cell_area_ha)
        pond_ha = float(pond_cells * cell_area_ha)
        ratio = catchment_ha / pond_ha if pond_ha else 0.0
        self_overlap = float(
            100 * np.logical_and(catchment_mask, polygon_mask).sum() / pond_cells
        )

        warnings = []
        if ratio > CATCHMENT_RATIO_WARN:
            warnings.append(
                f"catchment is {ratio:.0f}x the pond area — the site likely sits on a drainage "
                "line, so this behaves as an in-stream structure rather than a farm pond; "
                "runoff estimates from it will be far larger than the pond can hold"
            )
        if self_overlap < SELF_OVERLAP_WARN_PCT:
            warnings.append(
                f"only {self_overlap:.0f}% of the pond drains to its own pour point — the "
                "delineation may be unreliable here (D8 is weak on flat terrain)"
            )

        pour_lon, pour_lat = elevation_service.pixel_to_lonlat(
            pour[1], pour[0], xmin_tile, ymin_tile, zoom
        )
        results.append(
            {
                "index": index,
                "geometry": terrain_service.mask_to_polygon(
                    catchment_mask, xmin_tile, ymin_tile, zoom
                ),
                "area_ha": catchment_ha,
                "pond_area_ha": pond_ha,
                "catchment_to_pond_ratio": ratio,
                # Returned so the pour-point choice is inspectable rather than hidden (HLD 5.1).
                "pour_point": {"lat": pour_lat, "lon": pour_lon},
                "self_overlap_pct": self_overlap,
                "warnings": warnings,
            }
        )

    return {"results": results, "resolution_m": resolution_m}


@router.get("/rainfall")
async def get_rainfall(lat: float, lon: float, years: int = rainfall_service.DEFAULT_YEARS):
    """Historical rainfall for a point: annual mean plus the design-storm (max single-day) value."""
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise HTTPException(status_code=400, detail="lat/lon out of range")
    if not 1 <= years <= 40:
        raise HTTPException(status_code=400, detail="years must be between 1 and 40")
    return await rainfall_service.fetch_rainfall(lat, lon, years)
