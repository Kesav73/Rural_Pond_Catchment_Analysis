import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services import contours as contours_service
from app.services import elevation as elevation_service
from app.services import flow_cache
from app.services import gridref as gridref_service
from app.services import pond_sizing
from app.services import rainfall as rainfall_service
from app.services import terrain as terrain_service

router = APIRouter(prefix="/api", tags=["catchment"])

# Above this catchment:pond area ratio the site is effectively an in-stream structure (check-dam
# territory) rather than a farm pond. Measured ratios ranged 2.6x-145x across both flat urban and
# higher-relief rural test areas, so this is flagged rather than silently trusted — Tasks.md 4.7/9.5.
#
# CORRECTED 2026-08-29: this warning used to also claim the runoff "will far exceed anything the
# pond could hold". That claim was wrong once C was cited at 0.18 (was an uncited 0.30). With the
# real constants a pond only fills at a catchment ratio of ~79x at a 146.9 mm storm (117x at
# 100 mm, 58x at 200 mm) — all above this 50x threshold. So at 50-79x the old text fired
# *simultaneously* with the "may not fill" warning, telling the user both that the site would
# overflow and that it would never fill. The ratio warning is now strictly about geomorphology
# (a site on a drainage line silts up and floods, whatever the volumes say); whether the pond is
# hydraulically overwhelmed is measured directly by fill_ratio, which needs no proxy threshold.
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
    # Reuses the solve `/api/candidates` already did for this bbox (Tasks.md 4.9). Ranking now
    # needs catchment areas, so the flow network exists by the time the frontend asks for catchment
    # polygons — recomputing it here was pure duplication of a multi-second step.
    accumulation, direction = flow_cache.get_flow_solution(
        cache_key=flow_cache.make_key(min_lon, min_lat, max_lon, max_lat, zoom),
        smoothed=smoothed,
    )

    center_lat = (min_lat + max_lat) / 2
    gridref = gridref_service.TileGridRef(xmin_tile, ymin_tile, zoom, grid.shape)
    resolution_m = gridref.resolution_m
    cell_area_ha = resolution_m**2 / 10_000

    results = []
    for index, geometry in enumerate(request.polygons):
        try:
            ring = geometry["coordinates"][0]
        except (KeyError, IndexError, TypeError):
            raise HTTPException(status_code=400, detail=f"polygon {index} is not a GeoJSON Polygon")

        polygon_mask = terrain_service.polygon_to_mask(ring, grid.shape, gridref)
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
                "line, so it behaves as an in-stream structure (check-dam territory) rather than "
                "a farm pond: expect siltation and flood damage regardless of the volume figures"
            )
        if self_overlap < SELF_OVERLAP_WARN_PCT:
            warnings.append(
                f"only {self_overlap:.0f}% of the pond drains to its own pour point — the "
                "delineation may be unreliable here (D8 is weak on flat terrain)"
            )

        pour_lon, pour_lat = gridref.pixel_to_lonlat(pour[1], pour[0])
        results.append(
            {
                "index": index,
                "geometry": terrain_service.mask_to_polygon(catchment_mask, gridref),
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


class PondPlanRequest(BaseModel):
    pond_area_m2: float = Field(..., gt=0, description="Pond footprint area in m^2")
    catchment_area_m2: float = Field(..., ge=0, description="Catchment area draining to the pond")
    design_storm_mm: float = Field(..., ge=0, description="Max single-day rainfall in mm")
    depth_m: float | None = Field(None, gt=0, description="Override the standard pond depth")


@router.post("/pond-plan")
async def pond_plan(request: PondPlanRequest):
    """Size a pond for a site: Rational Method runoff, capacity, and capture percentage (FR6/FR7).

    `design_storm_mm` must be the **maximum single-day** rainfall, not an annual total — the annual
    basis is a recorded failure mode of this project (it sized a pond at 1061 m x 1061 m).
    """
    depth = request.depth_m or pond_sizing.POND_DEPTH_M
    runoff = pond_sizing.runoff_volume_m3(request.catchment_area_m2, request.design_storm_mm)
    capacity = pond_sizing.pond_capacity_m3(request.pond_area_m2, depth)
    ratio = (
        request.catchment_area_m2 / request.pond_area_m2 if request.pond_area_m2 else 0.0
    )

    warnings = []
    fill = pond_sizing.fill_ratio(runoff, capacity)
    if ratio > CATCHMENT_RATIO_WARN:
        warnings.append(
            f"catchment is {ratio:.0f}x the pond area — the site likely sits on a drainage line, "
            "so it behaves as an in-stream structure (check-dam territory) rather than a farm "
            "pond: expect siltation and flood damage regardless of the volume figures"
        )
    # Hydraulic sufficiency is measured, not inferred from the ratio — the two say different
    # things and used to contradict each other (see CATCHMENT_RATIO_WARN).
    if fill < 1.0:
        warnings.append(
            f"one design storm delivers only {fill:.0%} of the pond's capacity — this site may "
            "not fill in a single event"
        )
    elif fill > 5.0:
        warnings.append(
            f"one design storm delivers {fill:.0f}x the pond's capacity — most runoff will "
            "overflow; the pond is small relative to what drains into it"
        )

    return {
        "pond_area_m2": request.pond_area_m2,
        "catchment_area_m2": request.catchment_area_m2,
        "catchment_to_pond_ratio": ratio,
        "design_storm_mm": request.design_storm_mm,
        "depth_m": depth,
        "runoff_volume_m3": runoff,
        "capacity_m3": capacity,
        "capture_fraction": pond_sizing.capture_fraction(capacity, runoff),
        "fill_ratio": fill,
        "warnings": warnings,
        # Never present a judgement value as authoritative (Tasks.md 6.1 / 9.3).
        "assumptions": pond_sizing.constants_provenance(),
    }
