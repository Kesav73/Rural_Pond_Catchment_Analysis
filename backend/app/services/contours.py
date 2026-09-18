import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

from app.services.gridref import GridRef

# Verification (real Chhattisgarh farmland tile, z14 ~8.9m/px) found sigma=1 left pixel-level
# speckle that broke real drainage channels into noisy salt-and-pepper bands. At the original
# 10m band interval, sigma=5 was enough to get coherent shapes. Tightening the interval to 2m
# (finer bands) reintroduced the same speckle at sigma=5 — needed sigma=10 to get equally clean
# results at that resolution (sigma=15/20 looked barely different, i.e. diminishing returns).
DEFAULT_SIGMA = 10.0

# Contours smaller than this (in pixels²) are noise specks, not real terrain features.
MIN_CONTOUR_AREA_PX = 6

# approxPolyDP tolerance in pixels — small enough to keep band shapes faithful.
APPROX_EPSILON_PX = 1.0


def smooth(grid: np.ndarray, sigma: float = DEFAULT_SIGMA) -> np.ndarray:
    return gaussian_filter(grid, sigma=sigma)


def extract_contour_bands(
    grid: np.ndarray,
    gridref: GridRef,
    interval: float = 5.0,
    clip_bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """Threshold the elevation grid into interval-wide bands, trace each band's outline via
    findContours/approxPolyDP, and return one GeoJSON polygon per contiguous band region —
    filled bands (not thin iso-lines), so they read cleanly once colored by elevation.

    The grid is tile-aligned and overhangs the requested area by up to a tile, so `clip_bbox`
    trims every band to the area actually asked for.
    """
    from shapely.geometry import Polygon, box, mapping

    clip = box(*clip_bbox) if clip_bbox is not None else None
    min_e = float(np.floor(np.nanmin(grid) / interval) * interval)
    max_e = float(np.ceil(np.nanmax(grid) / interval) * interval)

    features = []
    band = min_e
    while band < max_e:
        mask = ((grid >= band) & (grid < band + interval)).astype(np.uint8) * 255
        if mask.any():
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                if cv2.contourArea(contour) < MIN_CONTOUR_AREA_PX:
                    continue
                approx = cv2.approxPolyDP(contour, APPROX_EPSILON_PX, True)
                if len(approx) < 3:
                    continue
                ring = [
                    gridref.pixel_to_lonlat(pt[0][0], pt[0][1])
                    for pt in approx
                ]
                ring.append(ring[0])  # GeoJSON polygon rings must close
                geometry = {"type": "Polygon", "coordinates": [ring]}
                if clip is not None:
                    # buffer(0) repairs the occasional self-touching ring approxPolyDP produces.
                    clipped = Polygon(ring).buffer(0).intersection(clip)
                    if clipped.is_empty or clipped.geom_type not in ("Polygon", "MultiPolygon"):
                        continue
                    geometry = mapping(clipped)
                features.append(
                    {
                        "type": "Feature",
                        "geometry": geometry,
                        "properties": {
                            "elevation_min": band,
                            "elevation_max": band + interval,
                            "elevation": band + interval / 2,
                        },
                    }
                )
        band += interval

    return {
        "type": "FeatureCollection",
        "features": features,
        "elevation_range": {"min": min_e, "max": max_e},
    }


# Every Nth contour line is a "major" (index) line: drawn heavier and labelled, as on a survey map.
MAJOR_LINE_EVERY = 5

# Lines shorter than this (pixels of arc length) are speckle around single cells, not terrain.
MIN_LINE_LENGTH_PX = 8.0


def extract_contour_lines(
    grid: np.ndarray,
    gridref: GridRef,
    interval: float = 2.0,
    clip_bbox: tuple[float, float, float, float] | None = None,
) -> dict:
    """Iso-lines at every multiple of `interval`, one MultiLineString feature per level.

    Each line is the outline of `grid >= level`, so it is closed wherever the ground rises above
    the level inside the grid and runs off the edge otherwise. The grid is tile-aligned and
    overhangs the requested area, so `clip_bbox` trims every line to it — which also removes the
    stretches that trace the grid's own border rather than real terrain.
    """
    from shapely.geometry import LineString, MultiLineString, box, mapping

    min_e = float(np.nanmin(grid))
    max_e = float(np.nanmax(grid))
    first = float(np.ceil(min_e / interval) * interval)
    clip = box(*clip_bbox) if clip_bbox is not None else None
    major_step = interval * MAJOR_LINE_EVERY

    features = []
    level = first
    while level <= max_e:
        mask = (grid >= level).astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        lines = []
        for contour in contours:
            if cv2.arcLength(contour, True) < MIN_LINE_LENGTH_PX:
                continue
            approx = cv2.approxPolyDP(contour, APPROX_EPSILON_PX, True)
            if len(approx) < 2:
                continue
            coords = [gridref.pixel_to_lonlat(pt[0][0], pt[0][1]) for pt in approx]
            coords.append(coords[0])
            line = LineString(coords)
            if clip is not None:
                line = line.intersection(clip)
            if line.is_empty:
                continue
            if line.geom_type == "LineString":
                lines.append(line)
            elif line.geom_type == "MultiLineString":
                lines.extend(line.geoms)
            # Points / collections from grazing the clip edge carry no line to draw.
        if lines:
            ratio = level / major_step
            features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(MultiLineString(lines)),
                    "properties": {
                        "elevation": level,
                        "major": bool(np.isclose(ratio, round(ratio))),
                    },
                }
            )
        level += interval

    return {
        "type": "FeatureCollection",
        "features": features,
        "elevation_range": {"min": min_e, "max": max_e},
        "interval": interval,
        "major_interval": major_step,
    }
