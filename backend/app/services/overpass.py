import asyncio
import json

import httpx

from app.db import postgres

# Mirrors are tried in order until one returns a usable response. Survey on 2026-08-29 found
# every mirror failing differently, which is why the list exists and why `_looks_empty` below does:
#   overpass-api.de        -> TCP connection refused
#   overpass.kumi.systems  -> /api/status 200 but /api/interpreter 500 on every query
#   z. / lz4.overpass-api  -> TCP connection refused
#   overpass.osm.ch        -> HTTP 200 serving an EMPTY database (zero buildings for central London)
OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]

# A bbox at least this large (in square degrees) is big enough that a zero-element answer is more
# likely a broken mirror than genuinely empty ground. ~0.0001 deg^2 is roughly 1.1 x 1.1 km.
#
# This was originally 0.001 (~3.5 x 3.5 km), which was too coarse and let the exact failure this
# guard exists to catch slip through: the Phase 2 sample contour map covers 0.000743 deg^2 — just
# under that threshold — so an Overpass response with zero buildings AND zero water was accepted as
# real data, and the API then reported `osm_available: true` while OSM had contributed nothing.
#
# The guard cannot perfectly separate "mirror is broken" from "this square kilometre really is
# empty", so it errs toward reporting the source as unavailable. Under-claiming a check that did not
# run is the safe direction; over-claiming one is how a silent gap gets shipped.
_NON_TRIVIAL_BBOX_DEG2 = 0.0001

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

# Overpass asks for this in-query; the client budget below must exceed it.
QUERY_TIMEOUT_S = 15

# Per-mirror budget. Deliberately tight: OSM is a *bonus* source now (WorldCover + SWIR are
# primary, 3.12), and this runs inside the candidate request. Mirrors are raced concurrently rather
# than tried in sequence, so the cost is the slowest *single* mirror, not their sum. Measured on the
# Bhilai bbox with Overpass fully unreachable: 80s sequential/70s-budget -> 31.6s sequential/25s ->
# ~15s raced. A short connect timeout matters most: two of three mirrors fail at TCP connect.
_TIMEOUT = httpx.Timeout(connect=5.0, read=float(QUERY_TIMEOUT_S), write=10.0, pool=5.0)


def _looks_empty(elements: list[dict], min_lon, min_lat, max_lon, max_lat) -> bool:
    """True when a successful response returned nothing over an area that cannot plausibly be empty.

    This exists because of a real failure found on 2026-08-29 (Tasks.md 3.11/9.7): overpass.osm.ch
    answered HTTP 200 with well-formed JSON, a bogus `timestamp_osm_base`, and zero elements — for
    *central London*. Without this guard that response is indistinguishable from "this area genuinely
    has no water", so `available` would be True, `build_water_index` would return None, the water
    screen would be silently skipped, and the UI would report a clean check while proposing ponds on
    top of rivers. An empty answer over a non-trivial bbox is treated as a broken mirror, not as data.
    """
    if elements:
        return False
    area_deg2 = abs(max_lon - min_lon) * abs(max_lat - min_lat)
    return area_deg2 >= _NON_TRIVIAL_BBOX_DEG2


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

    async def _try(client: httpx.AsyncClient, url: str) -> tuple[str, list[dict] | None, str]:
        try:
            response = await client.post(url, content=query.encode("utf-8"))
            response.raise_for_status()
            elements = response.json().get("elements", [])
        except Exception as exc:  # noqa: BLE001 — a dead mirror is expected, never fatal
            return url, None, f"{url}: {type(exc).__name__}"
        if _looks_empty(elements, min_lon, min_lat, max_lon, max_lat):
            return url, None, f"{url}: empty database (0 elements over a non-trivial bbox)"
        return url, elements, ""

    elements = None
    errors = []
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks = [asyncio.create_task(_try(client, url)) for url in OVERPASS_URLS]
        try:
            for finished in asyncio.as_completed(tasks):
                _url, result, error = await finished
                if result is not None:
                    elements = result
                    break
                errors.append(error)
        finally:
            # Whoever lost the race is no longer useful; don't leave sockets open behind us.
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    if elements is None:
        # Degrades the OSM half of the water check. WorldCover + SWIR remain primary (3.12), so
        # this is a reduced screen, not an absent one — but it must be reported honestly (9.6).
        return {
            "buildings": [],
            "water": [],
            "available": False,
            "error": "; ".join(errors) or "no Overpass mirror returned usable data",
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
