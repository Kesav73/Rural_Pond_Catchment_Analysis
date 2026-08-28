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
    """Mark candidates that sit on an existing water body.

    Two independent sources, either of which can trigger exclusion (see Tasks.md 3.5):
      - OSM/Overpass: things a human explicitly mapped (rivers, canals, lakes, reservoirs)
      - ESA WorldCover: satellite-classified water, which catches the small unmapped farm ponds
        OSM misses in rural Chhattisgarh

    Neither is authoritative alone, so this is a union, not an intersection. Candidates already
    excluded for shape (3.4) keep their original reason — shape exclusion is checked first.
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

        is_water = (
            osm_fraction >= OSM_OVERLAP_THRESHOLD
            or cover_fraction >= worldcover.WATER_OVERLAP_THRESHOLD
        )
        if is_water and not candidate.get("excluded"):
            sources = []
            if osm_fraction >= OSM_OVERLAP_THRESHOLD:
                sources.append(f"OSM water {osm_fraction:.0%}")
            if cover_fraction >= worldcover.WATER_OVERLAP_THRESHOLD:
                sources.append(f"ESA WorldCover water {cover_fraction:.0%}")
            candidate["excluded"] = True
            candidate["exclusion_reason"] = (
                "already an existing water body (" + ", ".join(sources) + ")"
            )

    # Ranks were assigned before water exclusion, so renumber over the survivors.
    candidates.sort(key=lambda c: (not c["excluded"], c["score"]), reverse=True)
    rank = 0
    for candidate in candidates:
        if candidate["excluded"]:
            candidate["rank"] = None
        else:
            rank += 1
            candidate["rank"] = rank
    return candidates
