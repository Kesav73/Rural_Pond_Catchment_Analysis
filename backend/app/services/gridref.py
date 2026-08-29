"""Georeferencing for elevation grids — the seam between *where the terrain came from* and
*what the terrain engine does with it* (Tasks_Phase2.md 2.1).

The terrain engine (Priority-Flood, depression detection, D8, flow accumulation, catchment
delineation) only ever needs three things from a grid's georeferencing:

    pixel -> lon/lat        to emit real-world geometry
    lon/lat -> pixel        to rasterize an input polygon
    metres per pixel        to convert cell counts into areas

It does *not* need to know about map tiles. Before this abstraction those functions took
`(xmin_tile, ymin_tile, zoom)` — Web Mercator tile coordinates — which welded the whole engine to
one elevation source. A contour map parsed from a KML file has no tiles and no zoom level, so that
signature made a second source impossible without duplicating the engine.

Two implementations:
  - `TileGridRef`   — Web Mercator tiles (AWS Terrarium). Delegates to `elevation`'s existing
                      maths so there is exactly one copy of the Mercator formulae.
  - `AffineGridRef` — a plain lon/lat bounding box with uniform pixel size, which is what a
                      contour-derived raster is.

Adding a third source (GeoTIFF, a different tile scheme, a projected CRS) means writing one more
class here and changing nothing in the engine.
"""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from app.services import elevation as elevation_service

_METRES_PER_DEGREE_LAT = 111_320.0


@runtime_checkable
class GridRef(Protocol):
    """What the terrain engine requires of any elevation grid's georeferencing."""

    shape: tuple[int, int]  # (rows, cols)

    def pixel_to_lonlat(self, col: float, row: float) -> list[float]:
        """Grid pixel (col, row) -> [lon, lat]."""
        ...

    def lonlat_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        """[lon, lat] -> (col, row) in the grid."""
        ...

    @property
    def resolution_m(self) -> float:
        """Metres per pixel, measured at the grid's own centre."""
        ...


class TileGridRef:
    """Web Mercator slippy-tile georeferencing — the AWS Terrarium elevation source.

    Behaviour is identical to the previous `(xmin_tile, ymin_tile, zoom)` parameters; this only
    bundles them into an object. All maths is delegated to `elevation` rather than copied.
    """

    def __init__(self, xmin_tile: int, ymin_tile: int, zoom: int, shape: tuple[int, int]):
        self.xmin_tile = xmin_tile
        self.ymin_tile = ymin_tile
        self.zoom = zoom
        self.shape = shape

    def pixel_to_lonlat(self, col: float, row: float) -> list[float]:
        return elevation_service.pixel_to_lonlat(
            col, row, self.xmin_tile, self.ymin_tile, self.zoom
        )

    def lonlat_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        return elevation_service.lonlat_to_pixel(
            lon, lat, self.xmin_tile, self.ymin_tile, self.zoom
        )

    @property
    def resolution_m(self) -> float:
        # Mercator's scale varies with latitude, so measure at the grid's centre row — the same
        # convention the tile pipeline used before this refactor.
        rows, cols = self.shape
        _, center_lat = self.pixel_to_lonlat(cols / 2, rows / 2)
        return elevation_service.ground_resolution(self.zoom, center_lat)

    def __repr__(self) -> str:
        return (
            f"TileGridRef(zoom={self.zoom}, tile=({self.xmin_tile},{self.ymin_tile}), "
            f"shape={self.shape})"
        )


class AffineGridRef:
    """Uniform lon/lat grid over a bounding box — what a contour-derived raster is.

    Row 0 is the **north** edge, matching both the image convention and `TileGridRef`, so the
    terrain engine sees the same orientation whichever source produced the grid.

    Unlike `TileGridRef` this is linear in lon/lat rather than in Mercator. Over the few-kilometre
    extent of a contour survey the difference is negligible, and it is the correct model for a
    grid that was *built* by sampling lon/lat uniformly — which is exactly how the contour
    interpolator constructs it.
    """

    def __init__(
        self,
        min_lon: float,
        min_lat: float,
        max_lon: float,
        max_lat: float,
        shape: tuple[int, int],
    ):
        if max_lon <= min_lon or max_lat <= min_lat:
            raise ValueError("AffineGridRef needs a non-degenerate bbox")
        rows, cols = shape
        if rows < 2 or cols < 2:
            raise ValueError("AffineGridRef needs at least a 2x2 grid")
        self.min_lon = min_lon
        self.min_lat = min_lat
        self.max_lon = max_lon
        self.max_lat = max_lat
        self.shape = shape

    @property
    def _lon_per_col(self) -> float:
        # Pixel *centres* span the bbox edges, so the step uses (cols - 1), not cols. Using cols
        # would shift every emitted coordinate by half a pixel.
        return (self.max_lon - self.min_lon) / (self.shape[1] - 1)

    @property
    def _lat_per_row(self) -> float:
        return (self.max_lat - self.min_lat) / (self.shape[0] - 1)

    def pixel_to_lonlat(self, col: float, row: float) -> list[float]:
        lon = self.min_lon + col * self._lon_per_col
        lat = self.max_lat - row * self._lat_per_row  # row 0 is the north edge
        return [lon, lat]

    def lonlat_to_pixel(self, lon: float, lat: float) -> tuple[float, float]:
        col = (lon - self.min_lon) / self._lon_per_col
        row = (self.max_lat - lat) / self._lat_per_row
        return col, row

    @property
    def resolution_m(self) -> float:
        """Metres per pixel at the grid centre, averaged over the two axes.

        Longitude degrees shrink with latitude, so the x and y ground sizes of a cell differ
        unless the grid was built with that already compensated. Averaging matches what the
        terrain engine needs it for — converting a cell count into an area.
        """
        center_lat = (self.min_lat + self.max_lat) / 2
        metres_per_col = (
            self._lon_per_col * _METRES_PER_DEGREE_LAT * math.cos(math.radians(center_lat))
        )
        metres_per_row = self._lat_per_row * _METRES_PER_DEGREE_LAT
        return (metres_per_col + metres_per_row) / 2

    def __repr__(self) -> str:
        return (
            f"AffineGridRef(bbox=({self.min_lon:.5f},{self.min_lat:.5f},"
            f"{self.max_lon:.5f},{self.max_lat:.5f}), shape={self.shape})"
        )
