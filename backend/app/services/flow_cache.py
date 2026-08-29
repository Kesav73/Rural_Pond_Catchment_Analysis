"""In-process cache for the D8 flow solution (Tasks.md 4.9).

The epsilon-fill -> D8 -> flow-accumulation chain costs several seconds and depends only on the
bbox + zoom, yet it was being computed twice for the same view: once implicitly when ranking needs
catchment areas, and again by `/api/catchment` moments later when the frontend asks for the top-N
catchment polygons. This caches the solution between those two calls.

Deliberately in-process rather than in Postgres: these are float arrays of several million cells
(a 2048x1792 grid is ~29 MB per array), so round-tripping them through the database would cost far
more than recomputing. Measured on this project, a single cached *elevation tile* already takes
~1.5 s to read back from Neon (us-east-2) versus 25 ms to decode — the remote cache is the slow
path for bulk arrays, not the fast one.

Bounded to a couple of entries because the realistic access pattern is one user looking at one
view, then immediately asking for its catchments.
"""

from collections import OrderedDict

import numpy as np

from app.services import terrain

# Two entries covers "current view" plus "the one they just panned away from". Each entry holds two
# float64 arrays plus an int8 direction array, so this is capped at roughly 150 MB in the worst
# case for the largest bbox the tile limit allows.
MAX_ENTRIES = 2

_cache: OrderedDict[str, tuple[np.ndarray, np.ndarray]] = OrderedDict()


def make_key(min_lon: float, min_lat: float, max_lon: float, max_lat: float, zoom: int) -> str:
    return f"flow:{min_lon:.5f}:{min_lat:.5f}:{max_lon:.5f}:{max_lat:.5f}:{zoom}"


def get_flow_solution(
    cache_key: str, smoothed: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return (accumulation, direction), computing them only on a miss.

    `smoothed` must be the same smoothed grid candidate detection used, so the flow network and the
    candidate polygons describe the same surface.

    Note this uses the **epsilon** Priority-Flood variant, not the plain one used for depression
    depth: the plain fill leaves depressions perfectly flat, and D8 cannot route across a flat
    surface.
    """
    cached = _cache.get(cache_key)
    if cached is not None:
        _cache.move_to_end(cache_key)
        return cached

    filled = terrain.priority_flood_fill_epsilon(smoothed)
    direction = terrain.d8_flow_direction(filled)
    accumulation = terrain.flow_accumulation(direction, filled)

    _cache[cache_key] = (accumulation, direction)
    while len(_cache) > MAX_ENTRIES:
        _cache.popitem(last=False)
    return accumulation, direction


def clear() -> None:
    _cache.clear()
