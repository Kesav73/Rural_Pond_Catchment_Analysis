# Contour-Map Catchment API — Phase 2

`POST /api/analyzeContour`

Upload a contour map (KML or KMZ). The service analyses the terrain it describes, proposes a
suitable pond location, and returns the catchment area draining to it.

---

## 1. Endpoint

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/analyzeContour` |
| **Request** | `multipart/form-data` |
| **Response** | `application/json` |
| **Max upload** | 64 MB |

### Request

| Field | In | Type | Required | Default | Meaning |
|---|---|---|---|---|---|
| `contour_map` | form-data | file | **yes** | — | Contour map, `.kml` or `.kmz`. KMZ is detected by zip signature, not by extension. `file` is accepted as an alias. |
| `resolution_m` | query | float | no | derived | Grid cell size in metres. Omitted → derived from the map's own measured contour spacing. |
| `min_depth` | query | float | no | derived | Minimum depression depth in metres. Omitted → one contour interval. |
| `top_n` | query | int (1–50) | no | `5` | How many ranked sites to return (1 primary + `top_n − 1` alternatives). |

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/analyzeContour \
     -F "contour_map=@backend/data/contours_1m.kml"
```

With overrides:

```bash
curl -X POST "http://127.0.0.1:8000/api/analyzeContour?resolution_m=5&top_n=3" \
     -F "contour_map=@my_survey.kmz"
```

---

## 2. Response

### 2.1 `pond_site` — the proposed location

The highest-ranked site. **This is the first of the two required outputs.**

| Field | Type | Units | Meaning |
|---|---|---|---|
| `geometry` | GeoJSON Polygon | lon/lat (WGS84) | Footprint of the proposed pond |
| `centroid.lat` / `.lon` | float | degrees | Centre of the footprint |
| `area_ha` | float | hectares | Surface area |
| `mean_depth_m` | float | metres | Mean depth of the natural depression |
| `max_depth_m` | float | metres | Deepest point |
| `compactness` | float | 0–1 | `4πA/P²`; 1.0 = a perfect circle, near 0 = a long thin channel |
| `rank` | int | — | Always 1 for `pond_site` |

### 2.2 `catchment` — the contributing area

**This is the second required output.**

| Field | Type | Units | Meaning |
|---|---|---|---|
| `area_ha` | float | hectares | Land draining to this pond |
| `geometry` | GeoJSON Polygon | lon/lat | Outline of that catchment |
| `pour_point.lat` / `.lon` | float | degrees | The outlet — where water leaves the pond |
| `catchment_to_pond_ratio` | float | ratio | `catchment ÷ pond area`. Above ~50 the site sits on a drainage line |
| `self_overlap_pct` | float | 0–100 | Share of the pond draining to its own pour point. Low values mean the delineation is unreliable |

### 2.3 `sizing` — pond dimensioning

| Field | Units | Meaning |
|---|---|---|
| `design_storm_mm` | mm | Maximum single-day rainfall on record for this location |
| `runoff_volume_m3` | m³ | Rational Method: `catchment area × rainfall × C` |
| `recommended_depth_m` | m | Standard farm-pond depth |
| `capacity_m3` | m³ | `pond area × depth × storage efficiency` |
| `capture_fraction` | 0–1 | Share of one storm's runoff the pond can hold |
| `fill_ratio` | ratio | How many times over one storm fills it. `< 1` means it may never fill |

### 2.4 `alternatives`

Array of the remaining ranked sites, each `{rank, location, catchment, sizing}` with the same
field meanings as above.

### 2.5 `screening` — why sites were kept or dropped

| Field | Meaning |
|---|---|
| `zones_detected` | Depressions found before any filtering |
| `zones_after_area_filter` | Survivors of the minimum-area filter |
| `boundary_applied` | `"boundary polygon from file"` or `"none"` |
| `excluded_water` | Dropped for sitting on or within 50 m of existing water |
| `excluded_shape` | Dropped as too elongated (likely a drainage channel) |
| `eligible` / `returned` | Passed all screening / actually returned |
| `worldcover_available`, `swir_available`, `osm_available` | Whether each water source responded. **A `false` here means that check did not run** |
| `water_buffer_m` | Proximity buffer applied around detected water |
| `rejected[]` | Up to 20 dropped sites with `candidate_id`, `area_ha`, and a human-readable `reason` |

### 2.6 `source` — provenance

Everything the pipeline derived from the uploaded file. Present so results are reproducible and so
it is visible that no parameter was hard-coded.

`filename`, `bytes`, `contour_lines`, `contour_levels`, `elevation_range_m`, `contour_interval_m`,
`elevation_field_used`, `vertices`, `skipped_no_elevation`, `skipped_degenerate`, `bbox`,
`boundary_polygon_found`, `grid_shape`, `grid_resolution_m`, `measured_contour_spacing_m`,
`interpolated_fraction`, `min_depth_m_used`, `rainfall_available`.

`elevation_field_used` is one of `placemark_name`, `coordinate_z`, `extended_data`, `description`.

### 2.7 `assumptions` and `warnings`

`assumptions` reports every constant behind the sizing numbers with a `cited` flag and a `source`
string, so a placeholder is never presented as authoritative. `warnings` is an array of plain-English
cautions (large catchment ratio, unavailable water screening, indeterminate contour interval).

---

## 3. Errors

Every failure returns a JSON body `{"detail": "..."}`.

| Status | Cause |
|---|---|
| `400` | Empty file; not valid XML/KML; no placemarks; corrupt KMZ; no line geometry; only one elevation level; no resolvable elevation; non-positive `min_depth` |
| `413` | Upload exceeds 64 MB, or too large to interpolate at the requested resolution |
| `422` | The map parsed correctly but no site qualifies — nothing deep or large enough, or everything was screened out |
| `500` | Should not occur; treat as a bug |

`422` is deliberately distinct from `400`: the input was fine, the terrain simply offers no suitable
site. That is a real answer, not an error in the request.

---

## 4. How the catchment is estimated

```
contour lines
   │  TIN interpolation (Delaunay + linear)
   ▼
elevation grid
   │  Priority-Flood depression fill
   ▼
depth = filled − original          ← where water would pool, and how deep
   │  threshold, connected-component labelling
   ▼
candidate depressions
   │  shape filter + existing-water screening
   ▼
eligible sites
   │  Priority-Flood (ε variant) → D8 flow directions → flow accumulation
   ▼
pour point = highest-accumulation cell inside the site
   │  reverse flood-fill upstream
   ▼
catchment
```

**Interpolation.** Contour vertices are triangulated (Delaunay) and linearly interpolated onto a
regular lon/lat grid — the standard TIN approach for contour-to-DEM conversion. Cells beyond the
triangulation hull are nearest-filled and flagged, so extrapolated ground is distinguishable from
interpolated ground.

**Depression filling.** Priority-Flood (Barnes, Lehman & Mulla, 2014) — the algorithm behind ArcGIS
Fill, GRASS `r.fill.dir` and TauDEM PitRemove. It is Dijkstra's algorithm applied to elevation:
seed a priority queue with the border cells, always expand the lowest resolved-adjacent cell, and
raise each newly reached cell to `max(its own elevation, the elevation it was reached at)`. The
amount a cell had to be raised *is* how deep water would pool there.

**Two fills, deliberately.** The plain fill leaves depressions perfectly flat, and D8 cannot route
across a flat surface — every cell picks the same arbitrary neighbour and flow accumulation
collapses. So flow routing uses an ε-gradient variant that raises each cell a hair above the one it
was reached from, turning filled areas into gentle ramps toward their outlet.

**Pour point.** The outlet is the **highest flow-accumulation** cell inside the pond, not the
lowest-elevation one. That is counter-intuitive but necessary: flow directions are computed on the
*filled* surface, and filling exists precisely to remove the deepest point as a sink, so it is no
longer where flow converges. Measured on this project: choosing the lowest-elevation cell produced
a 0.14 ha catchment for a 14.7 ha pond (≈0% self-overlap); highest-accumulation gave 94% overlap.

**Catchment.** Walk the D8 flow directions in reverse from the pour point; every cell that drains to
it is in the catchment.

**Ranking.** `capacity × min(runoff ÷ capacity, 1)` — storage weighted by whether enough water
actually arrives. A site is rewarded until it can fill, then no further, so an enormous catchment
earns no bonus. Ranking by raw water volume instead put every winner on a major drainage line
(catchment:pond ratios of 2,111–8,448× versus 59–515× under this formula).

**Existing water is excluded before ranking**, not after — so mapped channels are gone from the pool
before anything is scored, which is what makes catchment-driven ranking safe.

---

## 5. Extensibility

The terrain engine is decoupled from any particular elevation source by a `GridRef` georeference
object exposing only `pixel_to_lonlat`, `lonlat_to_pixel`, and `resolution_m`.

- `TileGridRef` — Web Mercator slippy tiles (the satellite-elevation path)
- `AffineGridRef` — a lon/lat bounding box with uniform pixel size (the contour-derived path)

Adding a third source (GeoTIFF, a projected CRS, a different tile scheme) means writing one class
and changing nothing in Priority-Flood, D8, flow accumulation or catchment delineation.

Parser generality is handled the same way: elevation is resolved by trying `<name>`, the coordinate
Z column, `ExtendedData`, then `<description>`, choosing whichever resolves the most placemarks and
**reporting the winner** in `source.elevation_field_used`. Geometry is selected by geometry *type*,
never by folder name. The boundary is the largest polygon, not one matched by name. Namespaces are
optional and the document root may be `<kml>`, `<Document>` or `<Folder>`.

Every numeric parameter is derived from the uploaded file or supplied as an argument: grid
resolution from measured contour spacing, depth threshold from the contour interval, water-check
extent from the contour bounding box.

---

## 6. Limitations

**Flat-triangle artifact.** Linear TIN interpolation between two contour lines *at the same
elevation* produces flat triangles — real terrain there is a ridge or valley floor, not a plateau.
Measured on the sample: 18.8% of cells. It was checked rather than assumed: depressions overlap
those flats at 19.8–22.8% across every depth threshold, essentially the 18.8% background rate, so
the artifact is **not** manufacturing pond candidates. It is documented rather than mitigated.

**Depth is systematically under-estimated.** The innermost closed contour has no data inside it, so
interpolation flattens its interior to that ring's elevation — a pit floor becomes a plateau at the
last mapped level. Real depths can therefore be up to one contour interval greater than reported.
Under-reporting is the safe direction, but it is a real bias.

**Vertical resolution is bounded by the contour interval.** A depression shallower than one interval
cannot be resolved, which is why the depth threshold is derived from it.

**Edge extrapolation.** Cells outside the triangulation hull are nearest-filled. The fraction is
reported as `source.interpolated_fraction`.

**D8 on flat terrain.** Single-direction flow routing is weakest exactly where relief is low.
`self_overlap_pct` is reported so an unreliable delineation is visible rather than silent.

**Water screening is a mitigation, not a guarantee.** It uses 2021 satellite land cover plus
shortwave-infrared reflectance, both at 10 m resolution, so a channel narrower than that is
invisible to it and a pond dug since 2021 is not detected. The shape filter is the backstop for
unmapped channels. The satellite check also depends on the map carrying real-world coordinates —
true for KML by definition, but a contour file in local site coordinates would lose that layer.

**No land-ownership data exists for this region.** Nothing here establishes that a proposed site is
available, unowned, or government land. The output is a shortlist for a human to visit, not an
approval.
