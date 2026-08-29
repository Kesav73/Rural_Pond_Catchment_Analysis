"""Contour lines -> elevation grid (Tasks_Phase2.md 2.3).

The terrain engine works on a raster. A contour map is vectors. This module bridges the two using
the standard GIS approach: Delaunay triangulation over the contour vertices, then linear
interpolation onto a regular lon/lat grid (a TIN surface).

Every parameter is derived from the input or passed as an argument — nothing here knows the
provided sample's contour interval, elevation range, or location.

**Known artifact, deliberately surfaced rather than hidden.** Linear TIN interpolation between two
contour lines *at the same elevation* produces flat triangles: the triangulation connects vertices
across the gap between two neighbouring lines of equal height, and every point inside that triangle
interpolates to that same height. Real terrain there is a ridge or a valley floor, not a plateau.
This matters here specifically because Priority-Flood will then report those plateaus as
depressions — i.e. the artifact can manufacture pond candidates. `flat_fraction()` measures how much
of a produced grid is affected so the caller can report it honestly.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.interpolate import griddata
from scipy.spatial import cKDTree

from app.services.gridref import AffineGridRef

_METRES_PER_DEGREE_LAT = 111_320.0

# Guard rails on the produced raster: small enough to stay meaningful, large enough not to exhaust
# memory or make Priority-Flood crawl. These bound a *derived* resolution, they do not set it.
MIN_GRID_PX = 64
MAX_GRID_PX = 2048

# How finely to sample relative to the spacing between contour lines. Interpolating much finer than
# the source data invents detail that is not there; much coarser throws away real relief.
SAMPLES_PER_CONTOUR_SPACING = 4.0

# Which percentile of the measured contour spacing sets the resolution. NOT the median: on a map
# that is mostly gentle slope, contours are widely spaced almost everywhere, so the median is
# dominated by the flat majority and under-resolves locally steep features. Measured on a synthetic
# map with a tight bowl in a broad slope: median spacing 549 m vs 241 m at the 10th percentile — a
# 2.3x spread, so the median sets a grid more than twice as coarse as the bowl's own contours need.
#
# The 25th percentile resolves the tightest quarter of the map. Erring fine is the safe direction:
# over-resolving costs computation, under-resolving silently loses real terrain. The 10th percentile
# was measured too — it would take the provided sample from 2.5 to 1.5 m/px and roughly triple the
# cell count for little gain, since that map's spacing is fairly uniform (median/p10 = 1.7).
SPACING_PERCENTILE = 25


def _contour_spacing_deg(lines, sample_limit: int = 4000) -> float:
    """Ground distance (in degrees) between neighbouring contour lines, at SPACING_PERCENTILE.

    Measured, not assumed: for each of a sample of vertices, the distance to the nearest vertex
    that belongs to a *different* elevation. That is the horizontal scale at which this particular
    map carries information, and it is what the grid resolution should follow.
    """
    points = []
    elevations = []
    for line in lines:
        for lon, lat in line.coords:
            points.append((lon, lat))
            elevations.append(line.elevation)
    points_array = np.asarray(points, dtype=np.float64)
    elevation_array = np.asarray(elevations, dtype=np.float64)

    rng = np.random.default_rng(0)  # fixed seed: the derived resolution must be reproducible
    if len(points_array) > sample_limit:
        idx = rng.choice(len(points_array), sample_limit, replace=False)
    else:
        idx = np.arange(len(points_array))

    tree = cKDTree(points_array)
    # Ask for several neighbours and keep the closest one at a different elevation; the nearest
    # neighbours of a vertex are its own line's neighbours, which say nothing about line spacing.
    k = min(64, len(points_array))
    distances, neighbours = tree.query(points_array[idx], k=k)
    spacings = []
    for row_i, point_i in enumerate(idx):
        own = elevation_array[point_i]
        for dist, neighbour in zip(distances[row_i], neighbours[row_i]):
            if elevation_array[neighbour] != own:
                spacings.append(dist)
                break
    if not spacings:
        raise ValueError("could not measure contour spacing — all vertices share one elevation")
    return float(np.percentile(spacings, SPACING_PERCENTILE))


def choose_resolution_m(parsed, requested_m: float | None = None) -> float:
    """Grid resolution in metres: honour an explicit request, else derive it from the map itself."""
    if requested_m is not None:
        if requested_m <= 0:
            raise ValueError("resolution_m must be positive")
        return float(requested_m)

    spacing_deg = _contour_spacing_deg(parsed.lines)
    min_lon, min_lat, max_lon, max_lat = parsed.bbox()
    center_lat = (min_lat + max_lat) / 2
    # Degrees -> metres, averaged over the two axes (longitude degrees shrink with latitude).
    metres_per_deg_lon = _METRES_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
    spacing_m = spacing_deg * (metres_per_deg_lon + _METRES_PER_DEGREE_LAT) / 2
    return max(spacing_m / SAMPLES_PER_CONTOUR_SPACING, 1e-6)


def build_surface(parsed, resolution_m: float | None = None) -> dict:
    """Interpolate parsed contours onto a regular grid.

    Returns a dict with:
      grid            float64 elevation raster, row 0 = north edge
      gridref         AffineGridRef describing it
      inside_hull     bool mask — True where the value came from interpolation rather than
                      nearest-neighbour extrapolation beyond the data's convex hull
      resolution_m    metres per pixel actually used
      spacing_m       measured contour spacing the resolution was derived from
    """
    min_lon, min_lat, max_lon, max_lat = parsed.bbox()
    center_lat = (min_lat + max_lat) / 2
    metres_per_deg_lon = _METRES_PER_DEGREE_LAT * math.cos(math.radians(center_lat))

    spacing_m = None
    if resolution_m is None:
        spacing_deg = _contour_spacing_deg(parsed.lines)
        spacing_m = spacing_deg * (metres_per_deg_lon + _METRES_PER_DEGREE_LAT) / 2
        resolution_m = max(spacing_m / SAMPLES_PER_CONTOUR_SPACING, 1e-6)

    width_m = (max_lon - min_lon) * metres_per_deg_lon
    height_m = (max_lat - min_lat) * _METRES_PER_DEGREE_LAT
    cols = int(np.clip(round(width_m / resolution_m), MIN_GRID_PX, MAX_GRID_PX))
    rows = int(np.clip(round(height_m / resolution_m), MIN_GRID_PX, MAX_GRID_PX))

    gridref = AffineGridRef(min_lon, min_lat, max_lon, max_lat, shape=(rows, cols))

    points = np.asarray(
        [(lon, lat) for line in parsed.lines for lon, lat in line.coords], dtype=np.float64
    )
    values = np.asarray(
        [line.elevation for line in parsed.lines for _ in line.coords], dtype=np.float64
    )

    lon_axis = np.linspace(min_lon, max_lon, cols)
    lat_axis = np.linspace(max_lat, min_lat, rows)  # row 0 is north
    lon_mesh, lat_mesh = np.meshgrid(lon_axis, lat_axis)

    linear = griddata(points, values, (lon_mesh, lat_mesh), method="linear")
    inside_hull = ~np.isnan(linear)

    # Cells beyond the triangulation's convex hull have no interpolated value. Fill them by nearest
    # neighbour so the raster is complete, and keep `inside_hull` so callers can tell the two apart
    # rather than treating extrapolated ground as measured.
    if not inside_hull.all():
        nearest = griddata(points, values, (lon_mesh, lat_mesh), method="nearest")
        grid = np.where(inside_hull, linear, nearest)
    else:
        grid = linear

    return {
        "grid": np.ascontiguousarray(grid, dtype=np.float64),
        "gridref": gridref,
        "inside_hull": inside_hull,
        "resolution_m": gridref.resolution_m,
        "requested_resolution_m": resolution_m,
        "spacing_m": spacing_m,
    }


def flat_fraction(grid: np.ndarray, tolerance: float) -> float:
    """Share of cells sitting on a perfectly flat patch — the TIN flat-triangle artifact.

    A cell counts as flat when every one of its 4-connected neighbours is within `tolerance` of it.
    Pass the file's own contour interval scaled down (e.g. interval/100) as the tolerance, so the
    measure adapts to the map rather than assuming a metre value.
    """
    if grid.size == 0:
        return 0.0
    flat = np.ones_like(grid, dtype=bool)
    flat[:, :-1] &= np.abs(grid[:, 1:] - grid[:, :-1]) <= tolerance
    flat[:, 1:] &= np.abs(grid[:, 1:] - grid[:, :-1]) <= tolerance
    flat[:-1, :] &= np.abs(grid[1:, :] - grid[:-1, :]) <= tolerance
    flat[1:, :] &= np.abs(grid[1:, :] - grid[:-1, :]) <= tolerance
    return float(flat.mean())


def sample_at(grid: np.ndarray, gridref, lon: float, lat: float) -> float | None:
    """Nearest-cell value at a lon/lat, or None if it falls outside the grid."""
    col, row = gridref.lonlat_to_pixel(lon, lat)
    r, c = int(round(row)), int(round(col))
    if 0 <= r < grid.shape[0] and 0 <= c < grid.shape[1]:
        return float(grid[r, c])
    return None
