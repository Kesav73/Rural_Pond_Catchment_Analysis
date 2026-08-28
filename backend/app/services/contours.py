import cv2
import numpy as np
from scipy.ndimage import gaussian_filter

from app.services.elevation import pixel_to_lonlat

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
    grid: np.ndarray, xmin_tile: int, ymin_tile: int, zoom: int, interval: float = 5.0
) -> dict:
    """Threshold the elevation grid into interval-wide bands, trace each band's outline via
    findContours/approxPolyDP, and return one GeoJSON polygon per contiguous band region —
    filled bands (not thin iso-lines), so they read cleanly once colored by elevation."""
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
                    pixel_to_lonlat(pt[0][0], pt[0][1], xmin_tile, ymin_tile, zoom)
                    for pt in approx
                ]
                ring.append(ring[0])  # GeoJSON polygon rings must close
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Polygon", "coordinates": [ring]},
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
