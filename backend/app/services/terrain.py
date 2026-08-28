import heapq
import math

import cv2
import numpy as np
from scipy.ndimage import find_objects, label

from app.services import elevation as elevation_service

# 8-connected, matching D8 flow direction (Phase 4) so the same neighbor model is used
# throughout the terrain pipeline.
_NEIGHBORS = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]

# scipy.ndimage.label's default structuring element is 4-connected (a "+" shape); pass this
# explicitly to get 8-connected labeling, matching the priority-flood fill's neighbor model.
_LABEL_STRUCTURE = np.ones((3, 3), dtype=bool)

DEFAULT_MIN_DEPTH_M = 0.3

# Candidate detection needs its own (lighter) smoothing pass before Priority-Flood — reuses
# contours.smooth(), but NOT contours' own sigma (10): that value was tuned to suppress small-scale
# noise for *readable contour lines*, which also erases the small real depressions candidate
# detection needs to keep. Verified on the Bhilai test bbox: sigma=5 gave 232 connected depression
# zones vs. the recorded 245 (within ~5%); sigma=10 over-smoothed to only 44 zones.
CANDIDATE_SMOOTHING_SIGMA = 5.0

# Zones smaller than this are below pond scale (and at z14's ~8.9m/px, only a couple of pixels —
# too few for a meaningful shape/compactness measure anyway).
DEFAULT_MIN_AREA_M2 = 200.0

# approxPolyDP tolerance in pixels — matches contours.py. Simplification matters here beyond
# payload size: a raw pixel-staircase boundary inflates perimeter, which deflates compactness.
_APPROX_EPSILON_PX = 1.0

# Hard floor that rejects stream corridors. Compactness of an ideal rectangle at aspect ratio r
# is pi*r/(1+r)^2, so 0.5 ~= a 4:1 rectangle — about as elongated as a pond site can sensibly be.
# On the Bhilai test bbox this drops 21 of 220 zones, including the 43.8 ha stream corridor
# (compactness 0.148, below the 1st percentile) that used to rank first by raw area.
MIN_COMPACTNESS = 0.5

DEFAULT_TOP_N = 5


def priority_flood_fill(grid: np.ndarray) -> np.ndarray:
    """Barnes, Lehman & Mulla (2014) Priority-Flood — the same depression-filling step used
    internally by ArcGIS Fill, GRASS r.fill.dir, and TauDEM PitRemove. Structurally Dijkstra's
    algorithm applied to elevation instead of distance: seed a priority queue with the border
    cells, always expand the lowest-elevation resolved-adjacent cell next, and set each newly
    resolved cell's output height to max(its own elevation, the elevation it was reached at).
    Guarantees every interior cell has a non-decreasing path back to the grid border.

    This is the plain variant (no epsilon gradient) — good enough for FR3's depression-depth
    signal, but filled depressions come out perfectly flat, which breaks D8 flow routing. The
    epsilon-gradient variant needed for that (Phase 4) is a separate function, not this one.
    """
    rows, cols = grid.shape
    filled = grid.astype(np.float64, copy=True)
    visited = np.zeros(grid.shape, dtype=bool)
    heap = []

    for r in range(rows):
        for c in (0, cols - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (filled[r, c], r, c))
    for c in range(cols):
        for r in (0, rows - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (filled[r, c], r, c))

    while heap:
        elevation, r, c = heapq.heappop(heap)
        for dr, dc in _NEIGHBORS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not visited[nr, nc]:
                visited[nr, nc] = True
                filled[nr, nc] = max(filled[nr, nc], elevation)
                heapq.heappush(heap, (filled[nr, nc], nr, nc))

    return filled


def priority_flood_fill_epsilon(grid: np.ndarray, epsilon: float = 1e-3) -> np.ndarray:
    """Priority-Flood with an epsilon gradient — the variant required for flow routing.

    The plain fill (above) leaves filled depressions perfectly flat, and D8 cannot route across
    a flat surface: every cell picks the same arbitrary neighbour, so accumulation collapses.
    Recorded in Verification_Results.md as attempt 2 (max accumulation 1,493 cells, no coherent
    drainage) vs attempt 3 with epsilon (243,056 cells, coherent network).

    Each newly resolved cell is raised to at least `epsilon` above the cell it was reached from,
    so filled areas slope gently toward their outlet.
    """
    rows, cols = grid.shape
    filled = grid.astype(np.float64, copy=True)
    visited = np.zeros(grid.shape, dtype=bool)
    heap = []

    for r in range(rows):
        for c in (0, cols - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (filled[r, c], r, c))
    for c in range(cols):
        for r in (0, rows - 1):
            if not visited[r, c]:
                visited[r, c] = True
                heapq.heappush(heap, (filled[r, c], r, c))

    while heap:
        elevation, r, c = heapq.heappop(heap)
        for dr, dc in _NEIGHBORS:
            nr, nc = r + dr, c + dc
            if 0 <= nr < rows and 0 <= nc < cols and not visited[nr, nc]:
                visited[nr, nc] = True
                # The only difference from the plain fill: +epsilon, so a filled region is a
                # gentle ramp rather than a plateau.
                filled[nr, nc] = max(filled[nr, nc], elevation + epsilon)
                heapq.heappush(heap, (filled[nr, nc], nr, nc))

    return filled


def d8_flow_direction(filled: np.ndarray) -> np.ndarray:
    """D8: each cell points at whichever of its 8 neighbours is the steepest drop.

    Returns an int8 grid of indices into _NEIGHBORS, or -1 where nothing is lower (border
    outlets). Vectorised over the 8 directions rather than looping per cell — the recorded
    budget for this step is ~0.9s on 768x768.
    """
    rows, cols = filled.shape
    best_drop = np.zeros((rows, cols), dtype=np.float64)
    direction = np.full((rows, cols), -1, dtype=np.int8)

    for index, (dr, dc) in enumerate(_NEIGHBORS):
        # Shift the grid so neighbour values line up with their source cell.
        shifted = np.full_like(filled, np.inf)
        src_rows = slice(max(0, dr), rows + min(0, dr))
        src_cols = slice(max(0, dc), cols + min(0, dc))
        dst_rows = slice(max(0, -dr), rows + min(0, -dr))
        dst_cols = slice(max(0, -dc), cols + min(0, -dc))
        shifted[dst_rows, dst_cols] = filled[src_rows, src_cols]

        # Diagonal steps are longer, so compare gradient (drop per distance), not raw drop —
        # otherwise diagonals win too often and the drainage network skews.
        distance = math.sqrt(2.0) if dr and dc else 1.0
        drop = (filled - shifted) / distance

        better = drop > best_drop
        best_drop[better] = drop[better]
        direction[better] = index

    return direction


def flow_accumulation(direction: np.ndarray, filled: np.ndarray) -> np.ndarray:
    """Accumulated upstream cell count, processing cells from highest to lowest.

    Height order guarantees every upstream contributor is added before a cell is passed on, so
    one pass suffices (no iteration to convergence).
    """
    rows, cols = direction.shape
    accumulation = np.ones((rows, cols), dtype=np.float64)

    order = np.argsort(filled, axis=None)[::-1]  # highest first
    flat_direction = direction.ravel()
    flat_accumulation = accumulation.ravel()

    for flat_index in order:
        d = flat_direction[flat_index]
        if d < 0:
            continue
        r, c = divmod(int(flat_index), cols)
        dr, dc = _NEIGHBORS[d]
        nr, nc = r + dr, c + dc
        if 0 <= nr < rows and 0 <= nc < cols:
            flat_accumulation[nr * cols + nc] += flat_accumulation[flat_index]

    return flat_accumulation.reshape(rows, cols)


def delineate_catchment(
    direction: np.ndarray, pour_row: int, pour_col: int
) -> np.ndarray:
    """Everything draining to the pour point: walk the flow directions in reverse.

    Iterative (explicit stack) rather than recursive — a catchment can span hundreds of
    thousands of cells and would blow Python's recursion limit.
    """
    rows, cols = direction.shape
    in_catchment = np.zeros(direction.shape, dtype=bool)
    in_catchment[pour_row, pour_col] = True
    stack = [(pour_row, pour_col)]

    while stack:
        r, c = stack.pop()
        for index, (dr, dc) in enumerate(_NEIGHBORS):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < rows and 0 <= nc < cols) or in_catchment[nr, nc]:
                continue
            # Does this neighbour flow INTO the current cell? Its direction must point back here.
            ndr, ndc = _NEIGHBORS[direction[nr, nc]] if direction[nr, nc] >= 0 else (0, 0)
            if direction[nr, nc] >= 0 and nr + ndr == r and nc + ndc == c:
                in_catchment[nr, nc] = True
                stack.append((nr, nc))

    return in_catchment


def polygon_to_mask(
    ring: list[list[float]],
    grid_shape: tuple[int, int],
    xmin_tile: int,
    ymin_tile: int,
    zoom: int,
) -> np.ndarray:
    """Rasterise a lon/lat ring onto the elevation grid."""
    points = [
        elevation_service.lonlat_to_pixel(lon, lat, xmin_tile, ymin_tile, zoom)
        for lon, lat in ring
    ]
    mask = np.zeros(grid_shape, dtype=np.uint8)
    cv2.fillPoly(mask, [np.array(points, dtype=np.int32)], 1)
    return mask.astype(bool)


def select_pour_point(
    accumulation: np.ndarray, polygon_mask: np.ndarray
) -> tuple[int, int] | None:
    """Pour point = highest flow-accumulation cell inside the polygon.

    NOT the lowest-elevation cell, which is the intuitive choice and is wrong: flow directions
    are computed on the *filled* surface, and filling exists precisely to remove that deepest
    point as a sink, so it is no longer where flow converges. Recorded verification: the
    lowest-elevation choice produced a 0.14 ha catchment for a 14.7 ha pond (~0% self-overlap);
    highest-accumulation gave 94% self-overlap.
    """
    if not polygon_mask.any():
        return None
    masked = np.where(polygon_mask, accumulation, -np.inf)
    flat = int(np.argmax(masked))
    return divmod(flat, accumulation.shape[1])


def mask_to_polygon(
    mask: np.ndarray, xmin_tile: int, ymin_tile: int, zoom: int
) -> dict | None:
    """Trace a boolean mask's outer boundary into a GeoJSON polygon."""
    found, _ = cv2.findContours(
        mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    if not found:
        return None
    contour = max(found, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, _APPROX_EPSILON_PX, True)
    if len(approx) < 3:
        return None
    coordinates = [
        elevation_service.pixel_to_lonlat(point[0][0], point[0][1], xmin_tile, ymin_tile, zoom)
        for point in approx
    ]
    coordinates.append(coordinates[0])
    return {"type": "Polygon", "coordinates": [coordinates]}


def label_depressions(
    original: np.ndarray, filled: np.ndarray, min_depth: float = DEFAULT_MIN_DEPTH_M
) -> tuple[np.ndarray, np.ndarray, int]:
    """depth = filled - original marks every cell Priority-Flood had to raise to remove a pit —
    i.e. somewhere water would naturally pool. Threshold it (cells shallower than min_depth are
    noise, not a real pond-scale depression) and label the resulting mask into distinct zones.

    Returns (depth, labels, num_zones). labels is 0 for background, 1..num_zones per depression.
    """
    depth = filled - original
    mask = depth > min_depth
    labels, num_zones = label(mask, structure=_LABEL_STRUCTURE)
    return depth, labels, num_zones


def extract_zone_properties(
    depth: np.ndarray,
    labels: np.ndarray,
    num_zones: int,
    xmin_tile: int,
    ymin_tile: int,
    zoom: int,
    min_area_m2: float = DEFAULT_MIN_AREA_M2,
) -> list[dict]:
    """Turn each labeled depression into a scored candidate record: area, depth stats, boundary
    polygon (lon/lat), centroid, perimeter and compactness.

    compactness = 4*pi*area / perimeter^2 — 1.0 for a perfect circle, near 0 for a long thin
    shape. This is what separates a genuine pond bowl from a stream corridor (see 3.4).
    """
    if num_zones == 0:
        return []

    # Ground resolution varies with latitude, so measure it at the grid's own centre rather than
    # assuming a fixed value.
    rows, cols = labels.shape
    _, center_lat = elevation_service.pixel_to_lonlat(
        cols / 2, rows / 2, xmin_tile, ymin_tile, zoom
    )
    resolution_m = elevation_service.ground_resolution(zoom, center_lat)
    cell_area_m2 = resolution_m**2

    zones = []
    # find_objects gives each label's bounding box, so per-zone work stays local to that box
    # instead of scanning the full grid num_zones times.
    for zone_index, bounds in enumerate(find_objects(labels), start=1):
        if bounds is None:
            continue
        row_slice, col_slice = bounds
        sub_mask = labels[row_slice, col_slice] == zone_index

        pixel_count = int(sub_mask.sum())
        area_m2 = pixel_count * cell_area_m2
        if area_m2 < min_area_m2:
            continue

        sub_depth = depth[row_slice, col_slice][sub_mask]

        contours_found, _ = cv2.findContours(
            sub_mask.astype(np.uint8) * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        if not contours_found:
            continue
        # A zone is one connected component, so its outer boundary is the largest contour;
        # any others would be interior artefacts of the mask.
        contour = max(contours_found, key=cv2.contourArea)
        approx = cv2.approxPolyDP(contour, _APPROX_EPSILON_PX, True)
        if len(approx) < 3:
            continue  # degenerate: can't form a polygon

        perimeter_m = cv2.arcLength(approx, True) * resolution_m
        # Compactness must come from the traced polygon's OWN area, not the pixel-count area.
        # cv2 traces pixel centres, so a 3x3 pixel blob traces as a 2x2 square (area 4, not 9);
        # mixing pixel-count area with traced perimeter gave compactness up to 2.18 on real data,
        # which is geometrically impossible (the isoperimetric inequality caps 4*pi*A/P^2 at 1.0)
        # and would have wrecked the 3.4 ranking. Self-consistent area+perimeter keeps it <= 1.0.
        # area_ha below still reports the pixel-count area, which is the truer area measure.
        polygon_area_m2 = cv2.contourArea(approx) * cell_area_m2
        if perimeter_m <= 0 or polygon_area_m2 <= 0:
            continue
        compactness = (4 * math.pi * polygon_area_m2) / (perimeter_m**2)

        ring = [
            elevation_service.pixel_to_lonlat(
                col_slice.start + point[0][0],
                row_slice.start + point[0][1],
                xmin_tile,
                ymin_tile,
                zoom,
            )
            for point in approx
        ]
        ring.append(ring[0])  # GeoJSON rings must close

        rows_idx, cols_idx = np.nonzero(sub_mask)
        centroid_lon, centroid_lat = elevation_service.pixel_to_lonlat(
            col_slice.start + cols_idx.mean(),
            row_slice.start + rows_idx.mean(),
            xmin_tile,
            ymin_tile,
            zoom,
        )

        zones.append(
            {
                "candidate_id": zone_index,
                "area_ha": area_m2 / 10_000,
                "max_depth_m": float(sub_depth.max()),
                "mean_depth_m": float(sub_depth.mean()),
                "perimeter_m": perimeter_m,
                "compactness": compactness,
                "centroid": {"lat": centroid_lat, "lon": centroid_lon},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )

    return zones


def score_and_rank(
    zones: list[dict], min_compactness: float = MIN_COMPACTNESS
) -> list[dict]:
    """Rank candidates by estimated storage volume weighted by shape quality.

    Ranking by raw area alone is the known-broken behaviour recorded in
    Feature_Implementation.md: it put the stream corridor itself at the top, because
    Priority-Flood correctly sees a whole river valley as one big connected depression. Two
    changes fix that:

      1. a hard `min_compactness` floor, which removes elongated corridors outright, and
      2. score = (area x mean depth) x compactness — i.e. storage volume weighted by how
         bowl-shaped the zone is, so a compact deep basin beats a larger shallow sprawl.

    Zones failing the floor are kept in the returned list (marked excluded, with a reason) so
    the endpoint can report *why* something was dropped rather than silently discarding it.
    """
    ranked = []
    for zone in zones:
        scored = dict(zone)
        storage_volume_m3 = zone["area_ha"] * 10_000 * zone["mean_depth_m"]
        scored["storage_volume_m3"] = storage_volume_m3
        scored["score"] = storage_volume_m3 * zone["compactness"]
        if zone["compactness"] < min_compactness:
            scored["excluded"] = True
            scored["exclusion_reason"] = (
                f"elongated (compactness {zone['compactness']:.3f} < {min_compactness}) "
                "— likely a stream corridor, not a pond bowl"
            )
        else:
            scored["excluded"] = False
            scored["exclusion_reason"] = None
        ranked.append(scored)

    ranked.sort(key=lambda z: (not z["excluded"], z["score"]), reverse=True)
    for position, zone in enumerate(ranked, start=1):
        zone["rank"] = position if not zone["excluded"] else None
    return ranked
