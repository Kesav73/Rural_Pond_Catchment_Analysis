# Verification Results

All items below were **actually called/executed**, not assumed. Test date: 2026-08-07.
Test area used throughout: rural bbox near **Wardha, Maharashtra (20.75 N, 78.60 E)**.

---

## Summary

| ID | Item | Status |
|----|------|--------|
| R1 | Admin data (state/district/geometry) | **OK** |
| R1.3 | Sub-district / village level | **NO** — stop at district |
| R2 | Base + satellite tiles | **OK** |
| R3 | DEM raster | **OK** (via AWS, *not* OpenTopography) |
| R4 | Contours | **OK** — derived with OpenCV, no GDAL |
| R5 | Sinks / flow / catchment | **OK** — but only with the right algorithm (see R5 notes) |
| R6 | Buildings | **OK** |
| R7 | Rainfall | **OK** |
| R8 | Pond sizing | **PARTIAL** — formula works, model assumption is wrong (see R8) |
| R9 | Frontend libs | **OK** |
| R10 | Local env | **OK** — no GDAL needed at all |

**Headline: the entire system is buildable with free, keyless APIs and the packages already installed.**

---

## R1 — Administrative data `[OK]`

**Source chosen:** `udit-001/india-maps-data` → `geojson/india.geojson` (4.0 MB, one file)

```
HTTP 200, 4,089,052 bytes
features: 760
properties: {district, dt_code, st_nm, st_code, year}
geometry: Polygon (real coordinates)
states/UTs: 36        total districts: 760
Maharashtra: 36 districts
```

- R1.1 states `[OK]` — 36 states/UTs derivable from `st_nm`
- R1.2 districts `[OK]` — group by `st_nm`, 760 total
- R1.4 geometry `[OK]` — real polygons, so DEM clipping + centroid + bbox all work
- R1.5 centroid/bbox `[OK]` — computed directly from coordinates
- **R1.3 sub-district/village `[NO]`** — repo has only country + per-state district files. DataMeet has village boundaries but **shapefile only** (`.shp`, 10 MB), which needs geopandas/pyshp.

> **Decision Q1: dropdown stops at District.** The 3rd level is the *user drawing on the map*, which the revised flow already covers. This removes the need for village boundary data entirely.

**Rejected:** `datameet/maps` — shapefiles only, no GeoJSON, adds a geo-stack dependency for no benefit.

---

## R2 — Base map / imagery `[OK]`

Real tiles fetched for the test area:

| Layer | URL pattern | Result |
|---|---|---|
| OSM street | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | 200, 5,698 b, `image/png`, 256×256 |
| Esri World Imagery (satellite) | `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}` | 200, 13,026 b, `image/jpeg`, 256×256 |
| Esri satellite @ z17 | same, z17 | 200, 8,282 b — **high zoom works over rural India** |

- R2.1 `[OK]` no key needed
- R2.2 `[OK]` satellite available to at least z17
- R2.3 `[OK]` both free for academic use; OSM requires a real `User-Agent` (sent `PondPlanner/0.1`) and has a tile-usage policy — fine at demo scale
- R2.4 — **display only.** OpenCV does *not* need satellite pixels; it operates on the DEM. Note the Esri URL is `/{z}/{y}/{x}` — **y and x are swapped** vs OSM. Easy bug to hit.

---

## R3 — DEM `[OK]` (source changed)

### OpenTopography — `[NO]`
```
GET portal.opentopography.org/API/globaldem?demtype=SRTMGL1&...
HTTP 401
<error>Error: API Key required for access. Please register for an API key</error>
```
Keyless access has been withdrawn. Usable only if we register for a key.

### AWS Terrain Tiles (Terrarium) — `[OK]` **← chosen**
```
https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png
HTTP 200, 23,419 bytes, image/png
```
Elevation is RGB-encoded: `elev = (R*256 + G + B/256) - 32768`

Verified on the test area:
```
z13 tile  -> 256x256, elev 259-321 m, relief 62 m
3x3 stitch at z14 -> 768x768 raster, elev 252-341 m, fetch 13.5 s
```
Ground resolution at 20.75 N:

| zoom | m/px | 3×3 coverage |
|---|---|---|
| z13 | 17.9 | 13.8 km |
| z14 | **8.9** | **6.9 km** ← good default |
| z15 | 4.5 | 3.4 km |

- R3.1 `[OK]`, R3.2 `[OK]` — z14 at 8.9 m/px resolves village-scale depressions
- R3.3 `[OK]` — **no API key, no signup**
- R3.4 `[OK]` — **it's a PNG.** Pillow/OpenCV read it directly. **GDAL/rasterio not required.** This is the single biggest simplification in the project.
- R3.6 `[OK]` — real varied elevation over rural India, no voids/zeros

> Underlying data is SRTM/NED-derived, so true accuracy is ~30 m even where tiles are finer — z15 oversamples. Cite z14 as the working resolution.

### R3.5 point-elevation fallback `[OK]`
```
GET api.open-meteo.com/v1/elevation?latitude=20.75,20.76,20.77&longitude=78.60,78.61,78.62
{"elevation":[289.0, 279.0, 280.0]}   HTTP 200
```
Keyless. Useful for spot-checks/validation, not for rasters.

---

## R4 — Contours `[OK]`

- R4.1 — **No contour API exists.** Confirmed we must derive them. Expected.
- R4.2/R4.3 `[OK]` — OpenCV path verified on the real 768×768 DEM:

```
threshold DEM at 10 m intervals -> cv2.findContours per band
-> 711 contour polylines in 0.04 s     [numpy + cv2 only, no GDAL]
```

This is where **OpenCV does genuine work** (answers Q3): `cv2.findContours` on elevation-band masks is the contour extractor. `cv2.approxPolyDP` can simplify before sending to the browser.

- R4.4 — pixel→lat/lon is the inverse slippy-tile transform, already implemented for the fetch.

---

## R5 — Terrain analysis `[OK]` — *algorithm choice is critical*

This was the riskiest area and it needed **three attempts**. Recording all of them because the failures are the useful finding.

### Attempt 1 — iterative morphological fill `[FAILED]`
```
max sink depth 86.84 m | 292 depressions | largest 3,494 ha
max flow accumulation 483 cells = 0.04 km2
```
Wrong. An 86 m sink and a 3,494 ha depression mean the fill never converged — it swallowed the whole valley as one pit. 60 iterations of `grey_erosion` is not enough.

### Attempt 2 — priority-flood (Barnes et al.) `[PARTIAL]`
```
1.7 s | max sink depth 14.42 m | 1,238 depressions
largest 17.71 ha | median 0.263 ha          <- realistic
max flow accumulation 1,493 cells = 0.12 km2
stream cells (acc>500): 16    -> no coherent drainage network
```
Sink depths became correct. But accumulation stayed broken: filling makes depressions **perfectly flat**, and D8 cannot route across flat surfaces — every flat cell picked the same arbitrary neighbour.

### Attempt 3 — priority-flood **+ epsilon gradient** `[OK]` **← use this**
Each filled cell is raised to `max(z, e + 1e-3)` so filled areas slope gently toward their outlet.
```
1.9 s | 1,238 depressions | max depth 14.43 m | largest 18.09 ha
max flow accumulation 243,056 cells = 19.40 km2
  -> 41.2% of the raster drains to a single outlet
stream cells acc>500: 16,538 | acc>2000: 8,003
  -> drainage network coherent: True
catchment at pour point: 121,548 cells = 9.703 km2  (0.6 s)
```

- R5.1 `[OK]` priority-flood + epsilon, ~1.9 s
- R5.2/R5.3 `[OK]` D8 + accumulation, ~0.9 s
- R5.4 `[OK]` catchment delineation via reverse-flow traversal, ~0.6 s
- R5.5 `[OK]` **total terrain compute ≈ 3.4 s** on 768×768 — acceptable in a web request; DEM fetch (13.5 s) dominates and is cacheable

> **pysheds is not needed.** Everything above is numpy + scipy + heapq. Keeps the Windows install clean.

---

## R6 — Buildings `[OK]`

```
POST overpass-api.de/api/interpreter
[out:json][timeout:60];(way["building"](20.74,78.58,20.78,78.62););out count;
HTTP 200 -> ways: 212, total: 212
```

- R6.1 `[OK]` free, no key
- R6.2 `[OK]` — **212 buildings mapped in a random rural Maharashtra bbox.** Rural coverage is real, which was the main doubt.
- R6.3 — worked first try, but Overpass throttles under load. Cache per region; degrade gracefully on timeout. Since the revised flow makes buildings a *warning* not a hard filter, an Overpass failure cannot break the app.
- R6.4 — not needed given R6.2 passed.

---

## R7 — Rainfall `[OK]`

```
GET archive-api.open-meteo.com/v1/archive?latitude=20.75&longitude=78.60
    &start_date=2020-01-01&end_date=2020-12-31&daily=precipitation_sum
HTTP 200, 6,913 bytes
days: 366, non-null: 366   (zero gaps)
annual total: 1,160.9 mm
max 1-day: 87.4 mm
elevation returned: 289.0 m
```

- R7.1 `[OK]` keyless, by lat/lon
- R7.2 `[OK]` archive extends back decades — enough for a multi-year mean
- R7.3 `[OK]` max-1-day extractable directly from the daily series (87.4 mm here)
- R7.4 — global reanalysis (ERA5). IMD/data.gov.in would be the "official Indian" source but needs a key and gives district aggregates, not lat/lon. **Recommend Open-Meteo**, cite ERA5, note IMD as future work.

---

## R8 — Pond sizing `[PARTIAL]` — **model assumption is wrong**

The arithmetic works:
```
V = A_catchment x P_annual x C
  = 9.703 km2 x 1,161 mm x 0.30 = 3,379,406 m3
depth 3 m -> surface 1,126,469 m2 -> a 1,061 m x 1,061 m pond
```

**A 1 km × 1 km pond is absurd.** Verification caught a design flaw, not a code bug:

1. **Annual rainfall is the wrong basis.** A pond doesn't store a year of runoff at once — it fills and is drawn down repeatedly. Sizing must use a **design storm** (e.g. the max-1-day 87.4 mm, or a return-period value), not the annual 1,161 mm.
2. **The catchment was too large.** Picking the global max-accumulation cell grabs a 9.7 km² basin. A village farm pond serves a catchment of a **few hectares**. The user's drawn polygon must constrain the pour point, and we should cap/warn above a sensible catchment size.
3. **Direction should be inverted.** Don't compute a volume and derive a footprint. Instead: take the **user's drawn polygon** as the available area, assume a practical depth, and report *what fraction of the design-storm runoff it captures* plus expected fill events per year.

- R8.1 — rational method verified working; SCS-CN would need soil data we don't have. **Use rational method, state the assumption.**
- R8.2 — C = 0.30 used as placeholder; needs a citable Indian source
- R8.3 — 2–3 m assumed; needs a citable source (MGNREGA / ICAR farm-pond guidelines)
- R8.4 — evaporation/seepage: **declare out of scope**

> **Decision Q4 resolved: pond depth is fixed (2–3 m) and the drawn polygon fixes the area. We report capture performance, not a derived footprint.** This must be settled before the HLD.

---

## R9 — Frontend `[OK]`

| Asset | Result |
|---|---|
| Leaflet 1.9.4 JS | 200, 147,552 b |
| Leaflet 1.9.4 CSS | 200, 14,806 b |
| Leaflet-Geoman-free 2.18.3 JS | 200, 286,768 b |
| Leaflet-Geoman-free CSS | 200, 24,422 b |
| Leaflet.draw 1.0.4 JS + CSS (fallback) | 200, 67,484 b / 5,267 b |

- R9.1/R9.2 `[OK]` — Geoman **free** tier includes polygon + freehand drawing, which is exactly the "circle on the map" interaction
- R9.3 — sinks overlay as a PNG via `L.imageOverlay` with the bbox we already have
- R9.4 — contours/catchment/pond as GeoJSON layers

> Geoman's first fetch returned `000` (timeout on a 287 KB file); a retry gave 200. Worth vendoring the file locally rather than depending on a CDN at demo time.

---

## R10 — Local environment `[OK]`

| Package | Status |
|---|---|
| Python 3.13.4 | present |
| Flask 3.1.3 | present |
| numpy 2.2.4, scipy 1.15.2, opencv 4.12, pillow 11.2, requests 2.32 | present |
| rasterio | installable (`rasterio-1.5.0`) — **not needed** |
| pysheds | installable but pulls numba, llvmlite, scikit-image, pyproj — **not needed** |
| shapely | already satisfied |

**Nothing needs to be installed.** DEM is a PNG (R3.4) and terrain analysis is hand-rolled (R5), so the heavy geo stack is avoidable entirely.

---

## Decisions resolved by verification

| Q | Decision | Basis |
|---|---|---|
| **Q1** dropdown depth | **Stop at district**; region = user-drawn polygon | R1.3 village data is shapefile-only |
| **Q2** DEM live or pre-fetched | **Live + disk cache** by tile | 13.5 s fetch vs 3.4 s compute — fetch dominates |
| **Q3** does OpenCV do real work | **Yes** — `findContours` on elevation bands is the contour engine | R4.2 verified, 711 polylines in 0.04 s |
| **Q4** pond depth vs area | **Fix depth, take area from drawn polygon**, report capture % | R8 — the reverse gives a 1 km pond |
| **Q5** caching | **Required.** Cache DEM tiles, Overpass, rainfall to disk | Overpass throttles; DEM fetch is the bottleneck |

## Final stack

```
Backend   Flask + numpy + scipy + opencv + pillow + requests     (all installed)
DEM       AWS Terrain Tiles (terrarium PNG, keyless)
Tiles     OSM street + Esri World Imagery (keyless)
Admin     udit-001/india-maps-data india.geojson (one 4 MB file)
Rainfall  Open-Meteo Archive API (keyless)
Buildings Overpass API (keyless)
Frontend  Leaflet 1.9.4 + Leaflet-Geoman-free (vendored)
```
**Zero API keys. Zero new installs.**

## Open items before HLD

1. Cite a source for runoff coefficient C and standard farm-pond depth (R8.2/R8.3)
2. Choose the design-storm basis: max-1-day, or a return-period value (R8)
3. Decide the catchment-size cap and the warning threshold (R8)
