import json

import httpx

from app.db import postgres

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# One query pulls both layers so a candidate request costs a single Overpass call:
#  - buildings -> rendered as a non-blocking warning layer (FR3)
#  - water     -> feeds the candidate water-exclusion check (3.5); never rendered as a warning
# Overpass bbox order is (south, west, north, east), which is NOT our (minLon, minLat, ...) order.
_QUERY_TEMPLATE = """[out:json][timeout:{timeout}];
(
  way["building"]({south},{west},{north},{east});
  way["natural"="water"]({south},{west},{north},{east});
  way["water"]({south},{west},{north},{east});
  way["waterway"]({south},{west},{north},{east});
  way["landuse"="reservoir"]({south},{west},{north},{east});
  relation["natural"="water"]({south},{west},{north},{east});
);
out geom;
"""

QUERY_TIMEOUT_S = 60


def _elements_to_features(elements: list[dict]) -> tuple[list[dict], list[dict]]:
    buildings, water = [], []
    for element in elements:
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 2:
            continue
        coords = [[point["lon"], point["lat"]] for point in geometry]
        tags = element.get("tags", {})
        # A way is a polygon only if it actually closes; rivers/streams stay open linestrings
        # and must be treated as lines, not silently forced into polygons.
        closed = len(coords) >= 4 and coords[0] == coords[-1]
        feature = {"coords": coords, "closed": closed, "tags": tags}
        if "building" in tags:
            buildings.append(feature)
        else:
            water.append(feature)
    return buildings, water


async def fetch_buildings_and_water(
    min_lon: float, min_lat: float, max_lon: float, max_lat: float
) -> dict:
    """Fetch building + water geometry for a bbox, cached per rounded bbox.

    Soft failure by design: Overpass throttles under load and is the one external service this
    project treats as optional. On any failure this returns empty layers with `available: False`
    rather than raising — losing the building warning layer and the water-exclusion check is
    acceptable; failing the whole candidate request is not.
    """
    cache_key = f"overpass:{min_lon:.4f}:{min_lat:.4f}:{max_lon:.4f}:{max_lat:.4f}"
    pool = await postgres.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT data FROM {postgres.OVERPASS_CACHE} WHERE cache_key = $1", cache_key
        )
        if row is not None:
            return json.loads(row["data"])

    query = _QUERY_TEMPLATE.format(
        timeout=QUERY_TIMEOUT_S,
        south=min_lat,
        west=min_lon,
        north=max_lat,
        east=max_lon,
    )

    try:
        async with httpx.AsyncClient(timeout=QUERY_TIMEOUT_S + 10) as client:
            response = await client.post(OVERPASS_URL, content=query.encode("utf-8"))
            response.raise_for_status()
            elements = response.json().get("elements", [])
    except Exception as exc:  # noqa: BLE001 — any failure degrades to "no layer", never fatal
        return {
            "buildings": [],
            "water": [],
            "available": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    buildings, water = _elements_to_features(elements)
    result = {"buildings": buildings, "water": water, "available": True, "error": None}

    # Only successful responses are cached — caching a failure would pin an empty layer in place.
    async with pool.acquire() as conn:
        await conn.execute(
            f"""
            INSERT INTO {postgres.OVERPASS_CACHE} (cache_key, data)
            VALUES ($1, $2::jsonb)
            ON CONFLICT (cache_key) DO NOTHING
            """,
            cache_key,
            json.dumps(result),
        )
    return result
