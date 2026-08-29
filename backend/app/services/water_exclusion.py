from shapely.geometry import LineString, Polygon, shape
from shapely.strtree import STRtree

from app.services import worldcover

# Rivers/streams arrive as open linestrings with no width. Buffer them so "this candidate sits in
# the river channel" is actually detectable — a zero-width line almost never intersects a polygon
# edge-to-edge. ~15 m in degrees; deliberately modest so a pond merely *near* a stream (which is
# desirable — that's the water source) isn't excluded, only one sitting on the channel itself.
_WATERWAY_BUFFER_DEG = 15 / 111_320.0

# Minimum share of a candidate that must fall inside mapped OSM water before it's excluded.
# Same reasoning as the WorldCover threshold: touching a boundary isn't being a water body.
OSM_OVERLAP_THRESHOLD = 0.30


def _to_shapely(feature: dict):
    coords = feature["coords"]
    try:
        if feature["closed"] and len(coords) >= 4:
            return Polygon(coords)
        if len(coords) >= 2:
            return LineString(coords).buffer(_WATERWAY_BUFFER_DEG)
    except Exception:  # noqa: BLE001 — a malformed OSM way shouldn't break the request
        return None
    return None


def build_water_index(overpass_result: dict) -> STRtree | None:
    """Index OSM water geometry for fast candidate lookup. Returns None if there's nothing to
    check against (no water mapped here, or Overpass was unavailable)."""
    geometries = []
    for feature in overpass_result.get("water", []):
        geometry = _to_shapely(feature)
        if geometry is not None and not geometry.is_empty and geometry.is_valid:
            geometries.append(geometry)
    return STRtree(geometries) if geometries else None


def annotate_water_exclusion(
    candidates: list[dict], water_index: STRtree | None, worldcover_result: dict | None
) -> list[dict]:
    """Mark candidates that sit on — or too close to — an existing water body.

    Runs BEFORE scoring/ranking (Tasks.md 3.12). That ordering is what makes catchment-based
    ranking safe: mapped channels are gone from the pool before anything is ranked, so ranking by
    "how much water drains here" cannot promote a river. It therefore does not assign ranks —
    `score_and_rank` does that afterwards over the survivors.

    Sources, unioned (none is authoritative alone):
      - OSM/Overpass: things a human explicitly mapped. A *bonus* source — every mirror was
        unreachable or serving an empty database as of 2026-08-29 (see overpass._looks_empty)
      - WorldCover class 80 OR dark SWIR, already unioned and dilated by WATER_BUFFER_M upstream
        in `worldcover.fetch_water_mask` — this is the primary signal

    Waterways keep the small 15 m buffer applied in `_to_shapely`, deliberately unlike the 50 m
    body buffer: a pond *near a stream* is desirable (that is the inflow); only one sitting in the
    channel is disqualifying.
    """
    for candidate in candidates:
        ring = candidate["geometry"]["coordinates"][0]

        osm_fraction = 0.0
        if water_index is not None:
            try:
                polygon = shape(candidate["geometry"])
                if polygon.is_valid and polygon.area > 0:
                    hits = water_index.query(polygon)
                    overlap = 0.0
                    for index in hits:
                        intersection = polygon.intersection(water_index.geometries[index])
                        if not intersection.is_empty:
                            overlap += intersection.area
                    osm_fraction = min(1.0, overlap / polygon.area)
            except Exception:  # noqa: BLE001 — geometry edge cases must not fail the request
                osm_fraction = 0.0

        cover_fraction = 0.0
        if worldcover_result is not None and worldcover_result.get("available"):
            cover_fraction = worldcover.water_overlap_fraction(ring, worldcover_result)

        candidate["osm_water_fraction"] = osm_fraction
        candidate["worldcover_water_fraction"] = cover_fraction

        reasons = []
        if osm_fraction >= OSM_OVERLAP_THRESHOLD:
            reasons.append(f"OSM mapped water {osm_fraction:.0%}")
        if cover_fraction >= worldcover.WATER_OVERLAP_THRESHOLD:
            reasons.append(f"satellite water within {worldcover.WATER_BUFFER_M:.0f} m {cover_fraction:.0%}")

        if reasons and not candidate.get("excluded"):
            candidate["excluded"] = True
            candidate["exclusion_reason"] = (
                "on or beside an existing water body (" + ", ".join(reasons) + ")"
            )
        else:
            candidate.setdefault("excluded", False)
            candidate.setdefault("exclusion_reason", None)

    return candidates
