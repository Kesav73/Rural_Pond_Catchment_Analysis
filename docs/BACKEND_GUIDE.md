# Backend Guide

The algorithms, every service module, and the config/testing setup. For request/response shapes,
see [API_REFERENCE.md](API_REFERENCE.md); for the big picture, see [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Contents

- [The terrain algorithm, step by step](#the-terrain-algorithm-step-by-step)
- [Service modules](#service-modules)
- [External data sources](#external-data-sources)
- [Caching](#caching)
- [Configuration](#configuration)
- [Testing](#testing)

---

## The terrain algorithm, step by step

This is the core of `app/services/terrain.py`, used by both pipelines (see
[ARCHITECTURE.md](ARCHITECTURE.md#2-two-independent-analysis-pipelines)). Each stage below names
the exact function that does it.

### 1. Find hollows (depression detection)

1. **Smooth** the elevation grid (`contours.smooth`, Gaussian, σ=5 for candidate detection —
   sharper than the σ=10 used for the *visual* contour layer, because over-smoothing here would
   merge distinct pond bowls).
2. **Priority-Flood fill** (`priority_flood_fill`, ε=0): a heap-based algorithm (Barnes/Lehman/
   Mulla) that raises every cell to at least the level it would need to drain — the standard,
   correct way to remove spurious pits from a DEM without a "flat" side effect.
3. **Depth** = `filled − original`. Any cell where this is positive sat in a hollow.
4. **Label** connected regions (`label_depressions`, 8-connected `scipy.ndimage.label`) where
   `depth > MIN_DEPTH_M`.
5. **Extract per-zone properties** (`extract_zone_properties`): area, mean/max depth, perimeter,
   **compactness** (`4πA / P²` — 1.0 is a perfect circle; a stream channel scores low), centroid,
   and a GeoJSON polygon (via `cv2.findContours` + `approxPolyDP`).
6. **Clip to the requested viewport** — the elevation grid is tile-aligned and can overhang the
   bbox by up to a full tile; a zone whose centroid falls outside the bbox is dropped so "top 5"
   never includes something the user can't see.

### 2. Exclude existing water — *before* ranking, not after

Two independent signals are unioned (`water_exclusion.annotate_water_exclusion`):

- **OSM/Overpass**: mapped water polygons and buffered waterways (15 m either side).
- **Satellite**: ESA WorldCover land-cover class 80 ("permanent water bodies") + a Sentinel-2 SWIR
  threshold, both dilated by a 50 m proximity buffer.

A zone is excluded if **either** signal overlaps it by ≥ 30%. This runs before the catchment/
ranking step deliberately: if it ran after, a river (which naturally has an enormous catchment)
could out-rank a real pond site purely because "more water drains there."

### 3. Solve the flow network (once per bbox, not once per zone)

1. **Epsilon-fill** (`priority_flood_fill_epsilon`, ε=1e-3): a *sloped* fill — flat plateaus from
   step 1 get a tiny gradient so flow direction is well-defined everywhere.
2. **D8 flow direction** (`d8_flow_direction`): each cell points to whichever of its 8 neighbours
   is steepest downhill.
3. **Flow accumulation** (`flow_accumulation`): one pass, processing cells from highest to lowest,
   summing "how many cells eventually flow through here."

This whole three-step solve is expensive (seconds, on a real DEM), so it's computed **once per
bbox** and cached (`flow_cache`) — reused for every zone's catchment area *and* reused again if
`/api/catchment` is called right after for the same bbox.

### 4. Catchment area, cheaply

A zone's catchment area is just `accumulation[zone_mask].max() × cell_area` — the accumulation
value already tells you how many upstream cells drain to a point, so the zone's own outlet
(its highest-accumulation cell) already carries the answer. No extra flood-fill is needed just to
get an *area* — that's only needed to draw the catchment's exact **shape**, which is why
`delineate_catchment` (a reverse walk from a specific pour point) only runs for the top N sites a
user actually asks to see, not all ~hundreds of candidates in view.

### 5. Size and rank

**Rational Method** (`pond_sizing.py` — see below) turns catchment area + one storm's rainfall into
a runoff volume, and pond area + a standard depth into a capacity. `expected_volume_m3 =
min(capacity, runoff)` becomes both the headline number shown to the user and the ranking score
(`rank_mode="sufficiency"`, the default) — so the number displayed and the number a site was
ranked by can never disagree.

A compactness floor (`MIN_COMPACTNESS = 0.5`) is applied as a **backstop** exclusion at this stage,
for anything elongated enough to be an unmapped drainage line that neither OSM nor satellite water
data caught.

---

## Service modules

### `terrain.py`
The engine described above. Also owns: `polygon_to_mask` (rasterize a GeoJSON polygon onto the
grid), `mask_to_polygon` (the reverse, for drawing results), `select_pour_point` (the
highest-accumulation cell *inside* a zone — not the lowest-elevation cell, which can sit on a flat
area with an ambiguous outlet).

| Constant | Value | Meaning |
|---|---|---|
| `DEFAULT_MIN_DEPTH_M` | 1.5 | minimum depression depth to count as a candidate |
| `DEFAULT_MIN_AREA_M2` | 200.0 | minimum depression area |
| `CANDIDATE_SMOOTHING_SIGMA` | 5.0 | Gaussian smoothing for candidate detection |
| `MIN_COMPACTNESS` | 0.5 | backstop shape filter (below this, treated as a drainage line) |
| `DEFAULT_TOP_N` | 5 | how many ranked sites are returned by default |

### `pond_sizing.py`
The **Rational Method** and pond-capacity math — no dependency on any other service.

```
runoff_volume_m3   = catchment_area_m2 × (rainfall_mm / 1000) × RUNOFF_COEFFICIENT
pond_capacity_m3   = pond_area_m2 × depth_m × STORAGE_EFFICIENCY
expected_volume_m3 = min(capacity_m3, runoff_m3)
fill_ratio         = runoff_m3 / capacity_m3
capture_fraction   = min(1.0, capacity_m3 / runoff_m3)
```

| Constant | Value | Cited? | Meaning |
|---|---|---|---|
| `RUNOFF_COEFFICIENT` | 0.18 | ✅ Barlow's Tables, Class B / Season III | fraction of rainfall that becomes runoff |
| `POND_DEPTH_M` | 3.0 | ✅ MGNREGA standard farm-pond depth | assumed dig depth |
| `STORAGE_EFFICIENCY` | 0.70 | ⚠️ judgement, uncited | derates a trapezoidal (1.5:1 side-slope) pond vs. a plain prism |

`constants_provenance()` returns exactly this cited/judgement distinction, surfaced in every
sizing-bearing API response under `assumptions` — so a judgement call is never presented as if it
were a citation.

> **`rainfall_mm` must be the maximum single-day rainfall, not an annual total.** Using an annual
> figure by mistake is a documented past failure of this project — it once sized a pond at
> 1061 m × 1061 m. `volume_basis: "design_storm"` is included in every response specifically so
> this can't happen silently again.

### `elevation.py`
Fetches and stitches AWS Terrarium elevation tiles into one lon/lat-referenced raster.

- `TERRAIN_TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"`
- Decodes each pixel: `elevation = r×256 + g + b/256 − 32768`.
- `MAX_TILE_ZOOM = 15` (the source 404s above this — z15 is ~4.45 m/px), `MAX_TILES_PER_AXIS = 12`
  (the same size guardrail the frontend mirrors in `geo.js`, so the UI can reject an over-large
  drawn area before ever calling the backend).
- Fetches up to 8 tiles concurrently (`asyncio.Semaphore(8)`); each tile is cached individually in
  Postgres (`tile_cache`, key `terrain:{z}:{x}:{y}`) since tiles are reused across many requests.

### `gridref.py`
The abstraction that decouples the terrain engine from *where* its grid came from. A `GridRef`
protocol (`pixel_to_lonlat`, `lonlat_to_pixel`, `resolution_m`) is implemented two ways:
- `TileGridRef` — Web Mercator, for live elevation-tile grids (Pipeline A).
- `AffineGridRef` — a plain linear lon/lat grid, for contour-derived rasters (Pipeline B), where
  row 0 is the north edge.

This is what lets `terrain.py` run identically over either pipeline's grid.

### `surface.py`
Turns a parsed KML's contour lines into a raster (Pipeline B only), via Delaunay/TIN interpolation
(`scipy.interpolate.griddata`: linear inside the convex hull, nearest-neighbour outside it). Grid
resolution is **derived from the file itself** — measured contour-vertex spacing (25th percentile,
not the median, so tight features aren't over-smoothed) ÷ 4, clamped to a 64–2048 px grid. This is
what lets the same code handle a tiny site survey and a large regional contour map without
hand-tuning.

### `kml.py`
Parses KML/KMZ into contour lines + elevations + an optional boundary polygon. Elevation can be
encoded four different ways in the wild (placemark name, coordinate Z, an extended-data field, or
free-text description) — the parser tries all four and keeps whichever resolves the most
placemarks, rather than assuming one convention. The boundary polygon is chosen by **largest area**,
never by name (file authors don't reliably name their boundary layer anything predictable).

### `flow_cache.py`
A tiny in-process cache (2-entry `OrderedDict`, evicted LRU) for the epsilon-fill → D8 →
accumulation result, keyed by bbox+zoom. Deliberately **not** persisted to Postgres — measured
cost of reading a cached array back from Neon (~1.5 s) exceeded the cost of just recomputing it
(~25 ms), so the round trip wasn't worth it.

### `water_exclusion.py`
Builds a spatial index (`shapely.STRtree`) of OSM water geometry and tests each candidate zone
against it, unioned with the WorldCover/SWIR satellite signal. See [step 2 above](#2-exclude-existing-water--before-ranking-not-after).

### `worldcover.py`
Fetches ESA WorldCover (10 m land cover) and a Sentinel-2 SWIR band from a keyless WMS
(`titiler.terrascope.be`), both PNG images, decoded and dilated by `WATER_BUFFER_M = 50.0` (flagged
in code as a judgement call, re-tunable). `WATER_OVERLAP_THRESHOLD = 0.30` is the exclusion
threshold used by `water_exclusion.py`.

### `overpass.py`
Fetches OSM buildings + mapped water from the Overpass API, **racing three mirrors concurrently**
(`overpass-api.de`, `overpass.kumi.systems`, `overpass.osm.ch`) and taking whichever answers first,
cancelling the rest. Guards against a mirror returning HTTP 200 with an empty result set for a
non-trivial bbox (`_looks_empty`) — treated as a failure, not as "no buildings here," since that
pattern was observed from broken mirrors during testing.

### `rainfall.py`
Fetches historical daily precipitation from Open-Meteo's Archive API (ERA5 reanalysis), reduced to
two figures: `annual_mean_mm` (from complete years only) and `max_single_day_mm` — **the design
storm** used everywhere else in the pipeline. Cache key rounds lat/lon to 2 decimals (~1 km) so
nearby candidate sites share one fetch instead of one each.

### `contours.py`
The **visual** contour layer for the map UI (distinct from the depression-detection smoothing in
`terrain.py` — a coarser σ=10 Gaussian is used here, tuned for legible bands/lines rather than
preserving small pond-scale features). Produces either filled elevation bands or iso-lines
(`extract_contour_bands` / `extract_contour_lines`), both clipped to the requested bbox.

---

## External data sources

| Source | Used by | Provides | Failure mode |
|---|---|---|---|
| **AWS Terrarium elevation tiles** | `elevation.py` | Global DEM, RGB-PNG encoded | 404 above z15 (clamped); out-of-coverage tiles silently read as 0 m |
| **ESA WorldCover + SWIR** (via titiler WMS) | `worldcover.py` | 10 m land cover + SWIR band, for water screening | Non-image response or timeout (60 s) → degrades gracefully, `worldcover_available`/`swir_available` set `false` |
| **Overpass API (OSM)** | `overpass.py` | Buildings, mapped water | All 3 mirrors can be down/refused/empty at once (observed during testing) — treated as `overpass_available: false`, analysis continues without this signal |
| **Open-Meteo Archive** | `rainfall.py` | Historical daily rainfall → design storm | Generic failure → `rainfall_available: false`; real-time lag of ~10 days (ERA5 reanalysis) |
| **Esri World Imagery** | frontend only (direct tile fetch) | Satellite basemap | n/a — never touches the backend |
| **Nominatim** | frontend only (direct fetch) | Place-name search, India only | n/a |

Every one of these is **free and keyless** — a deliberate constraint so the app never depends on a
registered API key or a paid tier.

---

## Caching

See [ARCHITECTURE.md § Caching layers](ARCHITECTURE.md#4-caching-layers) for the full picture. The
Postgres schema (`app/db/postgres.py`):

| Table | Holds | Notes |
|---|---|---|
| `districts`, `villages` | Admin boundary data | Seeded by `scripts/seed_districts.py` / `seed_villages.py`; villages currently Chhattisgarh-only |
| `tile_cache` | Elevation tiles, contour geometry, candidate results, WorldCover/SWIR images | General-purpose; `kind` column distinguishes what's stored; no TTL |
| `rainfall_cache` | Rainfall series | Success-only |
| `overpass_cache` | Overpass query results | Success-only |
| `proposals` | — | Defined in the schema, **not referenced by any current router or service** — reserved for a possible future "save this result" feature |

**Cache-key versioning:** only `/api/candidates` has an explicit version prefix
(`candidates:v{CACHE_VERSION}`, currently `v4`) — bumped by hand whenever the ranking/exclusion
logic changes, with the reason for each bump recorded as a comment in `candidates.py`. Other
cached endpoints rely on their key already encoding every parameter that affects the result.

---

## Configuration

`app/core/config.py` (`pydantic-settings`, reads `backend/.env`):

| Setting | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(empty — required)* | Postgres/Neon connection string; raised as a `RuntimeError` at first use if unset |
| `CORS_ORIGINS` | `http://localhost:5500,http://127.0.0.1:5500` | Comma-separated allowed frontend origins |
| `DEFAULT_ELEVATION_ZOOM` | `14` | Elevation tile zoom (~8.9 m/px at Chhattisgarh's latitude) |

No other environment variables are read anywhere in the backend.

---

## Testing

```bash
cd backend
pytest -m "not slow"   # offline, no network/DB dependency beyond what's mocked — fast
pytest                 # everything, including live network calls — slower
```

| File | Covers | Speed |
|---|---|---|
| `tests/test_kml.py` | KML/KMZ parsing — the real sample file, plus generalisation across every elevation-encoding convention, hemispheres, KMZ archives, and deliberately malformed input (must raise `KMLParseError`) | Fast, no network |
| `tests/test_analyze_contour.py` | `POST /api/analyzeContour` end-to-end — the real sample file's exact measured output, synthetic Gaussian-bowl maps with a known analytic answer, request-contract edge cases (field-name aliasing, missing upload), and error handling | Mixed — request-contract and input-validation tests are fast; full-pipeline round-trips are marked `slow` (they hit WorldCover + rainfall over the network) |
| `tests/test_phase3_backend.py` | `expected_volume_m3` correctness, ranking-uses-expected-volume, contour line/band extraction and bbox clipping | Fast, fully offline (synthetic grids) |

`scripts/` also has three **standalone**, non-pytest verification tools (not run automatically):
- `verify_water_exclusion.py` — independently re-checks that no *returned* site overlaps the
  WorldCover water mask, against a live running server.
- `render_water_check.py` — renders a side-by-side PNG of satellite imagery vs. imagery + water
  mask + proposed sites, for manual visual verification (output committed at
  `backend/data/water_exclusion_check.png`).
- `seed_districts.py` / `seed_villages.py` — one-off DB seeding from India's Local Government
  Directory data (require `duckdb`, listed separately in `scripts/requirements-scripts.txt` since
  it's not a runtime dependency of the app itself).
