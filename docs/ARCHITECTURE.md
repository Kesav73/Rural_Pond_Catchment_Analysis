# Architecture

How the pieces fit together: the system diagram, the two analysis pipelines end-to-end, the
request lifecycle for a normal user action, and the caching layers underneath it all.

See also: [API_REFERENCE.md](API_REFERENCE.md) for endpoint details, [BACKEND_GUIDE.md](BACKEND_GUIDE.md)
for the algorithms, [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) for the UI.

---

## 1. System overview

```mermaid
flowchart TB
    subgraph Client["Browser — React + Leaflet"]
        Sidebar["Sidebar panels<br/>(region, run controls, results)"]
        MapView["MapView<br/>(Leaflet + Leaflet-Geoman)"]
    end

    subgraph Backend["FastAPI Backend (app/)"]
        Routers["Routers<br/>candidates · catchment · contours ·<br/>analyze_contour · regions"]
        Engine["Terrain Engine<br/>(services/terrain.py, pond_sizing.py,<br/>water_exclusion.py, kml.py, surface.py)"]
    end

    PG[("PostgreSQL (Neon)<br/>tile_cache · rainfall_cache ·<br/>overpass_cache · districts · villages")]
    Flow["In-process flow_cache<br/>(2-entry LRU, per bbox)"]

    subgraph External["External data sources"]
        DEM["AWS Terrarium<br/>elevation tiles"]
        WC["ESA WorldCover + SWIR<br/>(via titiler WMS)"]
        OSM["Overpass API<br/>(OpenStreetMap)"]
        Rain["Open-Meteo Archive<br/>(rainfall history)"]
        Esri["Esri World Imagery<br/>(satellite basemap)"]
        Nom["Nominatim<br/>(place search)"]
    end

    Sidebar <--> MapView
    MapView -- "draw region / click pond" --> Routers
    Sidebar -- "GET/POST /api/*" --> Routers
    Routers --> Engine
    Routers <--> PG
    Engine <--> Flow
    Routers --> DEM
    Routers --> WC
    Routers --> OSM
    Routers --> Rain
    MapView -- "tiles, direct" --> Esri
    Sidebar -- "place search, direct" --> Nom
```

**What talks to what directly, and why:**
- The browser fetches **map tiles** (Esri satellite, OpenStreetMap streets, place-name labels) and
  does **place search** (Nominatim) directly — these are just display and convenience, nothing for
  the backend to compute or cache.
- Everything that touches elevation, water screening, catchments or rainfall goes through the
  FastAPI backend, which checks its Postgres/in-process caches first and only calls out to an
  external source on a miss.

---

## 2. Two independent analysis pipelines

The project has **two ways in**, both converging on the same terrain engine
(`terrain.py` + `pond_sizing.py`):

| | Trigger | Elevation source | Endpoint |
|---|---|---|---|
| **A — Interactive (Phase 3)** | User draws a box/polygon on the live map | AWS Terrarium tiles, fetched for the drawn bbox | `GET /api/candidates` |
| **B — Uploaded contour map (Phase 2)** | User uploads a KML/KMZ contour file | The file's own contour lines, rasterized to a grid | `POST /api/analyzeContour` |

Both pipelines produce the same shape of result: ranked pond sites, each with a catchment, a
design-storm runoff figure, a pond capacity, and the smaller of the two as the headline
**expected water volume**.

### Pipeline A — draw-a-region (`GET /api/candidates`)

```mermaid
flowchart TD
    A["bbox from the drawn region"] --> B["Check tile_cache<br/>(candidates:v4:...)"]
    B -- hit --> Z["Return cached result"]
    B -- miss --> C["Fetch + stitch DEM tiles<br/>(elevation.get_elevation_grid)"]
    C --> D["Kick off in parallel:<br/>WorldCover water mask · Overpass · rainfall"]
    C --> E["Gaussian smooth (σ=5)"]
    E --> F["Priority-Flood fill (ε=0)"]
    F --> G["depth = filled − original<br/>label depressions (8-connected)"]
    G --> H["Zone properties: area, depth,<br/>perimeter, compactness, centroid"]
    H --> I["Keep only zones ≥ MIN_DEPTH_M<br/>and ≥ MIN_AREA_M2, centroid in view"]
    I --> J["Exclude existing water<br/>(OSM overlap ≥30% OR WorldCover/SWIR ≥30%)"]
    D -.results feed into.-> J
    J --> K["Epsilon-fill → D8 flow direction<br/>→ flow accumulation<br/>(cached per bbox in flow_cache)"]
    K --> L["Catchment area per zone =<br/>max(accumulation inside zone)"]
    L --> M["Rational Method:<br/>runoff, capacity, expected_volume_m3"]
    M --> N["Rank by expected_volume_m3<br/>(compactness ≥ 0.5 backstop exclusion)"]
    N --> O["Top N + summary + excluded list"]
    O --> P{"Water screen<br/>fully succeeded?"}
    P -- yes --> Q["Cache result"]
    P -- no --> R["Return, but don't cache<br/>(never pin a degraded answer)"]
```

Sizing/ranking then continue identically on the client for individual pond details, and
`POST /api/catchment` is called separately per top-N site to trace and draw its exact catchment
outline (see [API_REFERENCE.md](API_REFERENCE.md#post-apicatchment) — this reuses the same
`flow_cache` entry computed above, so it doesn't repeat the epsilon-fill/D8/accumulation step).

### Pipeline B — uploaded contour map (`POST /api/analyzeContour`)

```mermaid
flowchart TD
    A["Uploaded KML/KMZ"] --> B["Parse contour lines + elevations<br/>+ boundary polygon (kml.py)"]
    B --> C["Depth threshold = measured<br/>contour interval (or override)"]
    C --> D["Rasterize via Delaunay/TIN<br/>interpolation (surface.py)"]
    D --> E["Priority-Flood fill →<br/>label depressions (same terrain.py path as Pipeline A)"]
    E --> F["Restrict to the file's own<br/>boundary polygon, if present"]
    F --> G["Exclude existing water<br/>(WorldCover/SWIR + Overpass)"]
    G --> H["Epsilon-fill → D8 → flow accumulation<br/>→ catchment per zone"]
    H --> I["Fetch rainfall for the file's<br/>centroid → design storm"]
    I --> J["Rank by expected_volume_m3"]
    J --> K["Describe top site + alternatives:<br/>geometry, sizing, screening, source metadata"]
```

The two pipelines share every terrain/sizing function; they differ only in **where the elevation
grid comes from** (live tiles vs. an uploaded file) and in pipeline B's extra step of restricting
results to the file's own study-area boundary.

---

## 3. Request lifecycle: "Find Pond Sites"

The most common user action, end to end:

```mermaid
sequenceDiagram
    actor U as User
    participant FE as React app (App.jsx)
    participant MV as MapView (Leaflet)
    participant API as FastAPI backend

    U->>MV: Draw rectangle/polygon
    MV->>FE: onRegionChange(region)
    U->>FE: Click "Find Pond Sites"
    FE->>API: GET /api/contours?bbox=...  (parallel, own AbortController)
    FE->>API: GET /api/candidates?bbox=...
    API-->>FE: ranked ponds + summary + excluded zones
    FE->>MV: setCandidates() · setExcluded() · fitToResults()
    FE->>API: GET /api/buildings?bbox=...  (fire-and-forget)
    FE->>API: POST /api/catchment {bbox, polygons, ranks}
    API-->>FE: catchment + warnings, per pond
    FE->>MV: setCatchments()
    U->>MV: Click a pond pin
    MV->>FE: onSelectSite(rank)
    FE->>MV: setSelected(rank) — draw that pond's catchment, open its popup
```

Design points worth knowing:
- **Every fetch carries an `AbortSignal`.** Redrawing the region, or clicking "Find Pond Sites"
  again, bumps a run id and aborts every in-flight request tied to the old one — a slow response
  can never draw results for a region that's no longer selected.
- **Catchments are computed for all top-N sites up front**, but only **drawn** for the pond the
  user has selected — so the map defaults to "here are the ranked locations" and only shows the
  (usually much larger) catchment area once you ask about a specific one.
- **Contours load in parallel with candidates**, not after — they're independent, so there's no
  reason to make the user wait twice.

---

## 4. Caching layers

| Layer | Where | Scope | Keyed by | Notes |
|---|---|---|---|---|
| `tile_cache` (Postgres) | elevation tiles, contour geometry, candidate results, WorldCover/SWIR images | shared, durable | source-specific (see [BACKEND_GUIDE.md](BACKEND_GUIDE.md#caching)) | No TTL — grows unbounded; invalidated only by changing the key (e.g. bumping `CACHE_VERSION`) |
| `rainfall_cache` / `overpass_cache` (Postgres) | rainfall series, OSM query results | shared, durable | rounded lat/lon (rainfall) or bbox (overpass) | Only a **successful** fetch is ever cached — a degraded/partial answer is never pinned in place |
| `flow_cache` (in-process, Python `OrderedDict`) | the epsilon-fill → D8 → accumulation result | per-process, 2-entry LRU | bbox + zoom | Shared between `/api/candidates` and `/api/catchment` for the same view, so tracing a catchment after finding candidates doesn't repeat the expensive flow solve. Not persisted — round-tripping large float arrays through Postgres was measured to cost more than just recomputing them. |
| Browser | none for analysis results | — | — | Every run re-fetches; the frontend has no client-side cache of its own beyond what's in memory for the current run |

---

## 5. Design decisions worth knowing

- **No GDAL/rasterio/pysheds.** Elevation arrives PNG-encoded (Terrarium format); the whole terrain
  engine — depression filling, D8 flow routing, contour tracing — is hand-written in numpy/SciPy/
  OpenCV. This keeps the backend pip-installable with no system-level geospatial libraries.
- **Water exclusion runs before ranking, not after.** Two independent signals (OSM mapped water,
  ESA WorldCover + Sentinel-2 SWIR satellite classification) are unioned and applied *before* the
  catchment-driven ranking step — so a river or existing tank can never be promoted to a top result
  just because "more water drains there."
- **Only cache what worked.** A partial/degraded result (e.g. WorldCover unreachable) is still
  returned to the user — with the gap disclosed in the response's `summary` flags — but is never
  written to cache, so a transient outage can't permanently degrade every future request for that
  area.
- **Pond location vs. catchment are visually and conceptually distinct on the map** (Phase 3 UI
  fix): the pond is drawn as water (blue); its catchment is drawn only once selected, in a
  different colour, labelled on the map — so the large catchment area is never mistaken for "the
  site itself."
- **Region/village/district admin-boundary endpoints exist but are unused by the current
  frontend.** `GET /api/states` / `/api/districts` / `/api/villages` (backed by `districts` and
  `villages` tables, seeded via `backend/scripts/seed_*.py`) were the original Phase 1
  location-picking UI. Phase 3 replaced that with draw-on-map + Nominatim place search, but the
  endpoints and seeded data are left in place rather than deleted, in case a "jump to a known
  place" convenience is wanted again later.
