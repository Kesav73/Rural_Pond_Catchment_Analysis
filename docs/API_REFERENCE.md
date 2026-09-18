# API Reference

All endpoints are under the `/api` prefix. Interactive, always-current docs are also available at
`http://127.0.0.1:8000/docs` (Swagger UI) whenever the backend is running.

Conventions used throughout:
- All geometry is **GeoJSON in WGS84**, `[lon, lat]` ordering.
- A `bbox` query parameter is always `minLon,minLat,maxLon,maxLat` as one comma-separated string.
- Error responses use FastAPI's standard `{"detail": "..."}` body with a 4xx/5xx status.
- "Cited" vs "judgement" constants (e.g. the runoff coefficient vs. the storage-efficiency factor)
  are surfaced in relevant responses under `assumptions`, so a placeholder number is never presented
  as authoritative — see [BACKEND_GUIDE.md](BACKEND_GUIDE.md#pond_sizingpy) for what each one means.

---

## Health

### `GET /api/health`
Liveness check. No params. → `{"status": "ok"}`

### `GET /api/health/db`
Checks the Postgres connection (`SELECT 1`). No params. → `{"status": "ok" | "unreachable"}`

---

## Admin regions *(seeded data, not used by the current frontend UI — see [ARCHITECTURE.md](ARCHITECTURE.md#5-design-decisions-worth-knowing))*

### `GET /api/states`
No params. → `[{"name": "<state>"}, ...]` — distinct states from the `districts` table.

### `GET /api/districts?state=<name>`
Required: `state`. → `[{"name", "centroid": {"lat","lon"}, "bbox": [minLon,minLat,maxLon,maxLat]}, ...]`
404 if the state has no districts on file.

### `GET /api/villages?district=<name>`
Required: `district`. → `[{"id","name","gp_name","block_name","centroid","bbox"}, ...]`
Village-level data currently only exists for Chhattisgarh (seeded by `scripts/seed_villages.py`) —
an **empty list is the normal, expected result** for any other district, not an error. `id` is the
database row id; it's the only reliable unique key, since neither the source's village name nor
its `vil_lgd` code is unique within a district.

---

## Contours

### `GET /api/contours?bbox=<bbox>&interval=2.0&style=bands|lines`

| Param | Default | Notes |
|---|---|---|
| `bbox` | required | `minLon,minLat,maxLon,maxLat` |
| `interval` | `2.0` | Band/line spacing in metres; must be > 0 |
| `style` | `"bands"` | `"bands"` = filled elevation-band polygons; `"lines"` = iso-lines |

Fetches the DEM for the bbox (same elevation source as `/api/candidates`), smooths it, then:

- **`style=bands`** → `FeatureCollection` of filled polygons, each `properties: {elevation_min,
  elevation_max, elevation}`, plus `elevation_range: {min, max}`. Clipped to the bbox.
- **`style=lines`** → `FeatureCollection` of `MultiLineString` features, one per elevation level,
  each `properties: {elevation, major}` (`major=true` every 5th interval — drawn heavier and
  labelled on the map). Also returns `interval` and `major_interval`. Clipped to the bbox.

Cached in Postgres per bbox/zoom/interval/style.

---

## Candidate pond sites

### `GET /api/candidates?bbox=<bbox>&min_depth=1.5&min_area=200&top_n=5&rank_mode=sufficiency`

| Param | Default | Notes |
|---|---|---|
| `bbox` | required | |
| `min_depth` | `1.5` m | minimum depression depth to count as a candidate |
| `min_area` | `200` m² | minimum depression area |
| `top_n` | `5` | how many ranked sites to return |
| `rank_mode` | `"sufficiency"` | `"sufficiency"` = rank by `expected_volume_m3` (capacity capped); `"water"` = rank by raw `runoff_m3` delivered. 400 if neither. |

**Response** — a GeoJSON `FeatureCollection`. Each feature's `properties` includes, per candidate:

| Field | Meaning |
|---|---|
| `area_ha`, `mean_depth_m`, `max_depth_m`, `compactness` | the pond depression itself |
| `catchment_area_m2` | land draining into it (flow-accumulation estimate — see `/api/catchment` for the traced polygon) |
| `runoff_m3` | one design storm's runoff reaching this site (Rational Method) |
| `capacity_m3` | how much the pond could hold |
| `expected_volume_m3` | **the headline figure** — `min(capacity_m3, runoff_m3)` |
| `fill_ratio`, `capture_fraction` | how much of one storm the pond captures / fills to |
| `score`, `rank` | ranking outcome; `rank` is `null` for excluded zones |
| `excluded`, `exclusion_reason` | why a zone didn't make the cut (existing water / too elongated) |
| `centroid` | `{lat, lon}` |

Plus, at the top level:

- **`summary`** — `zones_detected`, `zones_in_view`, `excluded_shape`, `excluded_water`,
  `eligible`, `returned`, `top_n`, `rank_mode`, `min_depth_m`, `resolution_m`, `design_storm_mm`,
  `volume_basis` (always `"design_storm"` — the figures are **per storm, not annual**),
  `overpass_available`, `worldcover_available`, `swir_available`, `rainfall_available`,
  `water_buffer_m`. The four `*_available` flags let the frontend disclose a degraded run instead
  of silently presenting a weaker screen as complete.
- **`assumptions`** — cited vs. judgement constants (see `pond_sizing.constants_provenance()`).
- **`excluded`** — up to 200 of the largest rejected zones, each with `geometry`, `centroid`,
  `area_ha`, `compactness` and `reason` — enough for the UI to draw *why nothing here qualified*
  when a search returns no sites.

Cached (`tile_cache`, key prefixed `candidates:v4:...`) **only** when the water screen fully
succeeded — a degraded result is returned but never pinned into the cache.

### `GET /api/buildings?bbox=<bbox>`
→ `{"type":"FeatureCollection","features":[{geometry: Polygon, properties:{tags}}],
"available": bool, "error": str|null}`. Building footprints from OpenStreetMap — a non-blocking
warning layer, not a hard filter. Only closed ways with ≥ 4 coordinates become polygons.

---

## Catchments and sizing

### `POST /api/catchment`

**Body:**
```json
{
  "bbox": "minLon,minLat,maxLon,maxLat",
  "polygons": [ /* GeoJSON Polygon geometries */ ],
  "ranks": [1, 2, 3]   // optional — echoed back per result, so the frontend can
                       // match results by rank rather than array position
}
```

Reuses the cached flow solution for that bbox (see `flow_cache` in
[ARCHITECTURE.md](ARCHITECTURE.md#4-caching-layers)) — this is why calling `/api/catchment` right
after `/api/candidates` for the same view is fast. For each polygon: rasterizes it, finds its pour
point (the highest-flow-accumulation cell inside it), and delineates the catchment by walking flow
direction backward from that point.

**Response:**
```json
{
  "results": [
    {
      "index": 0, "rank": 1,
      "geometry": { /* catchment polygon */ },
      "area_ha": 803.3, "pond_area_ha": 4.11,
      "catchment_to_pond_ratio": 195.4,
      "pour_point": {"lat": 21.11, "lon": 81.58},
      "self_overlap_pct": 94.2,
      "warnings": ["catchment is 195x the pond area — likely a drainage line, not a farm pond"]
    }
  ],
  "resolution_m": 8.9
}
```

Warnings fire when `catchment_to_pond_ratio > 50` (likely an in-stream drainage feature, not a
simple farm pond — expect siltation/flood behaviour) or `self_overlap_pct < 50` (the pond's own
footprint doesn't mostly drain to its own outlet — the delineation may be unreliable, typically on
very flat terrain).

### `GET /api/rainfall?lat=&lon=&years=10`
Validates lat/lon range and `1 ≤ years ≤ 40`. Returns the historical rainfall record for that
point — see [BACKEND_GUIDE.md](BACKEND_GUIDE.md#rainfallpy) for the exact shape.

### `POST /api/pond-plan`
Pure sizing math, no terrain computation — useful for testing the Rational Method formula in
isolation, or for a consumer that already has its own catchment/pond area figures.

**Body:** `{"pond_area_m2", "catchment_area_m2", "design_storm_mm", "depth_m"?}`
(`design_storm_mm` must be the **max single-day** rainfall, not an annual total — see the warning
in [BACKEND_GUIDE.md](BACKEND_GUIDE.md#pond_sizingpy)).

**Response:** `{pond_area_m2, catchment_area_m2, catchment_to_pond_ratio, design_storm_mm, depth_m,
runoff_volume_m3, capacity_m3, capture_fraction, fill_ratio, warnings[], assumptions}`.

---

## Uploaded contour map (Phase 2 deliverable)

### `POST /api/analyzeContour`

**Multipart form:** file field `contour_map` (also accepts the alias `file`) — a KML or KMZ
contour map. **Query params:** `resolution_m` (optional override), `min_depth` (optional override
of the measured-from-the-file default), `top_n` (1–50, default 5). Max upload size 64 MB.

**What it does:** parses the file's contour lines and elevations, rasterizes them via Delaunay/TIN
interpolation, then runs the **same** depression-detection → water-exclusion → flow-accumulation →
sizing → ranking pipeline as `/api/candidates` (see
[ARCHITECTURE.md](ARCHITECTURE.md#pipeline-b--uploaded-contour-map-post-apianalyzecontour)),
restricted to the file's own boundary polygon if it has one.

**Response** (top-level keys):

| Key | Contents |
|---|---|
| `pond_site` | the best-ranked zone: geometry, centroid, `area_ha`, depths, compactness, rank |
| `catchment` | `area_ha`, `catchment_to_pond_ratio`, `pour_point`, `geometry`, `self_overlap_pct` |
| `sizing` | `design_storm_mm`, `runoff_volume_m3`, `recommended_depth_m`, `capacity_m3`, `expected_volume_m3`, `volume_basis`, `capture_fraction`, `fill_ratio` |
| `alternatives` | the same shape as `pond_site`+`catchment`+`sizing`, for ranks 2..N |
| `screening` | `zones_detected`, `zones_after_area_filter`, `boundary_applied`, `excluded_water`, `excluded_shape`, `eligible`, `returned`, availability flags, `water_buffer_m`, up to 20 `rejected` zones |
| `source` | parse metadata: filename, byte size, contour line/level counts, elevation range, measured interval, which elevation field was used, vertex counts, grid shape/resolution, whether values were interpolated, rainfall availability |
| `assumptions` | cited vs. judgement constants |
| `warnings` | plain-text caveats |

**Errors:**
- `400` — missing/unreadable upload, bad query params, unparseable KML (empty, malformed XML, no
  placemarks, corrupt zip, only a single elevation level, elevation unresolvable from any known
  field).
- `413` — upload exceeds 64 MB.
- `422` — the file parsed fine, but no depression met the depth/area thresholds, or nothing
  survived water screening.

Demonstrated against the sample file at `backend/data/contours_1m.kml`:
```bash
curl -X POST http://127.0.0.1:8000/api/analyzeContour \
     -F "contour_map=@backend/data/contours_1m.kml"
```
