"""Independent check that no proposed pond sits on existing water (Tasks_Phase2.md 2.4.3).

Run as a standalone process so it owns its own event loop:

    .venv/bin/python scripts/verify_water_exclusion.py data/contours_1m.kml

Deliberately re-fetches the water mask itself rather than trusting the endpoint's own screening —
the point is to confirm the screen worked, which a self-report cannot establish. The assertion is
written against whatever mask is computed for the uploaded file, so it holds for any contour map.
"""

import asyncio
import os
import sys

# Run from anywhere: put the backend package root on the path rather than relying on cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.services import worldcover  # noqa: E402

API = "http://127.0.0.1:8000/api/analyzeContour"


async def main(path: str) -> int:
    with open(path, "rb") as handle:
        files = {"file": (path.split("/")[-1], handle.read())}
    async with httpx.AsyncClient(timeout=1200) as client:
        response = await client.post(API, files=files)
    if response.status_code != 200:
        print(f"FAIL: endpoint returned {response.status_code}: {response.text[:300]}")
        return 1
    body = response.json()

    min_lon, min_lat, max_lon, max_lat = body["source"]["bbox"]
    water = await worldcover.fetch_water_mask(min_lon, min_lat, max_lon, max_lat)
    if not water.get("available"):
        print("SKIP: water mask unavailable, cannot verify")
        return 0

    coverage = 100 * water["mask"].mean()
    screening = body["screening"]
    print(f"water mask covers {coverage:.2f}% of the map (incl. {screening['water_buffer_m']:.0f} m buffer)")
    print(f"endpoint reports {screening['excluded_water']} sites excluded as existing water")

    sites = [("pond_site", body["pond_site"])] + [
        (f"alternative #{a['rank']}", a["location"]) for a in body["alternatives"]
    ]
    failures = []
    for label, site in sites:
        fraction = worldcover.water_overlap_fraction(site["geometry"]["coordinates"][0], water)
        status = "OK" if fraction < worldcover.WATER_OVERLAP_THRESHOLD else "ON WATER"
        print(f"  {label:<16} {fraction:6.1%} water  {status}")
        if fraction >= worldcover.WATER_OVERLAP_THRESHOLD:
            failures.append(label)

    if coverage > 0 and screening["excluded_water"] == 0:
        print("FAIL: the map contains water but nothing was excluded — screen not wired up")
        return 1
    if failures:
        print(f"FAIL: {len(failures)} returned site(s) sit on water: {failures}")
        return 1
    print("PASS: no returned site sits on existing water")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "data/contours_1m.kml")))
