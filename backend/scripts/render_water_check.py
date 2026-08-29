"""Render a visual check of the water screening for any contour map.

    .venv/bin/python scripts/render_water_check.py data/contours_1m.kml [out.png]

Produces a side-by-side image: satellite imagery on the left, the same imagery on the right with
the computed water mask in red and the proposed pond sites outlined in green.

Why this exists: `verify_water_exclusion.py` checks that no returned site overlaps the water mask,
but it uses the *same* mask that did the excluding — so it confirms the wiring, not that the mask
is right. This script compares the mask against independent satellite imagery, which is the only
non-circular correctness check available without ground survey.

Everything is derived from the uploaded map: the extent comes from the parsed contours and the
imagery zoom is chosen to match. Needs the API running.
"""

import asyncio
import io
import math
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2  # noqa: E402
import httpx  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app.services import kml, worldcover  # noqa: E402

API = "http://127.0.0.1:8000/api/analyzeContour"
IMAGERY = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile"
TILE = 256
# Roughly how many pixels wide the rendered figure should be; the zoom level is picked to match so
# this works for a 3 km map and a 30 km one alike.
TARGET_WIDTH_PX = 1400


def deg2tile(lat, lon, zoom):
    n = 2**zoom
    x = (lon + 180) / 360 * n
    y = (
        1 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat))) / math.pi
    ) / 2 * n
    return x, y


def choose_zoom(min_lon, max_lon, target_px=TARGET_WIDTH_PX):
    for zoom in range(18, 0, -1):
        x0, _ = deg2tile(0, min_lon, zoom)
        x1, _ = deg2tile(0, max_lon, zoom)
        if (x1 - x0) * TILE <= target_px:
            return zoom
    return 12


def fetch_imagery(min_lon, min_lat, max_lon, max_lat, zoom):
    x0, y0 = deg2tile(max_lat, min_lon, zoom)
    x1, y1 = deg2tile(min_lat, max_lon, zoom)
    xs = list(range(int(x0), int(x1) + 1))
    ys = list(range(int(y0), int(y1) + 1))
    canvas = np.zeros((len(ys) * TILE, len(xs) * TILE, 3), np.uint8)
    for j, ty in enumerate(ys):
        for i, tx in enumerate(xs):
            try:
                raw = urllib.request.urlopen(f"{IMAGERY}/{zoom}/{ty}/{tx}", timeout=30).read()
                tile = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB"))
                canvas[j * TILE : (j + 1) * TILE, i * TILE : (i + 1) * TILE] = tile
            except Exception as exc:  # noqa: BLE001 — a missing tile leaves a black square
                print(f"  tile {zoom}/{ty}/{tx} failed: {exc}")
    top, left = (y0 - int(y0)) * TILE, (x0 - int(x0)) * TILE
    return canvas[int(top) : int(top + (y1 - y0) * TILE), int(left) : int(left + (x1 - x0) * TILE)]


async def main(path: str, out: str) -> int:
    parsed = kml.parse_contours(open(path, "rb").read())
    min_lon, min_lat, max_lon, max_lat = parsed.bbox()

    with open(path, "rb") as handle:
        files = {"file": (os.path.basename(path), handle.read())}
    async with httpx.AsyncClient(timeout=1800) as client:
        response = await client.post(API, files=files)
    if response.status_code != 200:
        print(f"endpoint returned {response.status_code}: {response.text[:300]}")
        return 1
    body = response.json()

    zoom = choose_zoom(min_lon, max_lon)
    print(f"fetching imagery at zoom {zoom}...")
    image = fetch_imagery(min_lon, min_lat, max_lon, max_lat, zoom)
    height, width = image.shape[:2]

    water = await worldcover.fetch_water_mask(min_lon, min_lat, max_lon, max_lat)
    if not water.get("available"):
        print("water mask unavailable, nothing to draw")
        return 1
    mask = cv2.resize(
        water["mask"].astype(np.uint8), (width, height), interpolation=cv2.INTER_NEAREST
    ).astype(bool)

    overlay = image.copy()
    overlay[mask] = (0.45 * overlay[mask] + 0.55 * np.array([255, 60, 60])).astype(np.uint8)

    sites = [body["pond_site"]] + [a["location"] for a in body["alternatives"]]
    for site in sites:
        ring = site["geometry"]["coordinates"][0]
        points = np.array(
            [
                [
                    [
                        (lon - min_lon) / (max_lon - min_lon) * width,
                        (max_lat - lat) / (max_lat - min_lat) * height,
                    ]
                    for lon, lat in ring
                ]
            ],
            np.int32,
        )
        cv2.polylines(overlay, points, True, (60, 255, 60), 3)

    Image.fromarray(np.hstack([image, overlay])).save(out)
    print(f"saved {out}")
    print(f"  water mask covers {100 * mask.mean():.2f}% of the map (incl. buffer)")
    print(f"  {body['screening']['excluded_water']} sites excluded as existing water")
    print(f"  {len(sites)} proposed sites drawn in green")
    return 0


if __name__ == "__main__":
    source = sys.argv[1] if len(sys.argv) > 1 else "data/contours_1m.kml"
    target = sys.argv[2] if len(sys.argv) > 2 else "data/water_exclusion_check.png"
    sys.exit(asyncio.run(main(source, target)))
