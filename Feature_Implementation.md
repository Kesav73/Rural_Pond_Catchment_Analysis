# Feature Implementation Notes

Working notes per functional requirement (FR1–FR8 from `CSD_Assignment_1.pdf`): API used, library used,
and how it's wired. Filled in one FR at a time as each is worked through and verified.

---

## FR1 — Display satellite imagery for a selected village

**API used:** Esri World Imagery tile service
`https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
Keyless, no signup. Tile scheme is `{z}/{y}/{x}` — **note the y/x order is reversed** vs. the OSM
convention (`{z}/{x}/{y}`), which is used elsewhere (e.g. Overpass-adjacent tooling, OSM street layer).

**Library used:** Leaflet.js (frontend only). It requests tiles from the Esri URL template and renders
them on a canvas. No backend image library is involved in this FR.

**Wiring:**
1. Backend resolves the selected district to a centroid (from Mongo-seeded `india.geojson` data — see
   precursor/region-selection notes).
2. Frontend receives the centroid, initializes the Leaflet map centered on it.
3. Frontend adds the Esri tile layer.
4. Browser fetches tiles **directly from Esri** — the backend is not in this request path at all. No
   proxying, no image bytes touch the server.

So FR1's backend footprint is just a coordinate handoff; the actual imagery delivery is entirely
client-side against a third-party tile API.

### Verification

Tested against a real, named location (Bhilai, Chhattisgarh — 21.1938°N, 81.3509°E) rather than an
arbitrary point, to confirm the tiles are correctly georeferenced and not placeholder/blank tiles.

- Fetched a 3×3 tile block (9 requests) at zoom 14 around the Bhilai centroid — **9/9 HTTP 200**.
- Stitched into a 768×768 image (8.9 m/px, ~6.9 km across). Visible content matched known Bhilai
  landmarks when cross-checked against an OSM street-tile render of the same bbox: Bhilai Steel Plant,
  Bhilai Power House, and Maroda-1 Reservoir all appeared in the expected relative positions.
- Fetched a tighter 5×5 block at zoom 18 (0.56 m/px, ~713 m across) near a specific point — resolved
  individual buildings, paths, and small water bodies.
- Confirmed the exact location of the zoom-18 crop via Nominatim reverse geocoding (keyless):
  `class: tourism, type: zoo`, Forest Avenue Road, Bhilai Sectors, Durg — matched the visible layout
  (winding paths, small ponds, structures consistent with a zoo/garden).

**Conclusion:** Esri World Imagery is confirmed correctly georeferenced and usable at both
district-overview zoom (z14) and site-selection zoom (z18) with no API key. FR1 requires no backend
compute — only the centroid handoff from region selection.

---

## FR2 — Visualize contour maps

**API used:** No contour API exists — confirmed by search and by direct testing; none of the elevation/mapping
providers checked return contour geometry directly. Contours must be derived from raw elevation data.

Elevation itself comes from **AWS Terrain Tiles**
(`https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png`), same source verified for FR1's
terrain work. Elevation is encoded per-pixel in a PNG: `elevation = R*256 + G + B/256 - 32768`. Keyless.

**Library used:**
- `numpy` — decode the PNG channels into a plain elevation grid
- `scipy.ndimage.gaussian_filter` — smooth the grid before contouring (raw tile data is noisy at pixel
  scale; unsmoothed contours are jagged and follow noise, not terrain)
- `cv2.findContours` — traces the boundary of a thresholded elevation mask; this *is* the contour extraction
- `cv2.approxPolyDP` — simplifies the traced boundary to fewer points without changing its shape
- `matplotlib` colormap (`turbo`) — used to color contours/regions by elevation value for readability (see
  below); this is a rendering concern, not part of the geometry extraction itself

**Wiring:**
1. Backend fetches a grid of elevation tiles covering the region's bbox, on the **same `z/x/y` tile scheme**
   used for the satellite imagery (FR1). Because both are Web Mercator slippy tiles, elevation and imagery
   align pixel-for-pixel automatically — no manual georeferencing step needed.
2. Decode PNG → numpy elevation array → Gaussian smoothing pass.
3. For each elevation band (e.g. every 2–5 m, tuned to the terrain's relief):
   a. Threshold: `mask = (elevation >= level)` → binary black/white image
   b. `cv2.findContours(mask, ...)` → list of pixel-coordinate polygons tracing that level's boundary
   c. `cv2.approxPolyDP(...)` → simplified polygon
   d. Convert pixel coords → lat/lon (inverse slippy-tile transform) → GeoJSON ring
4. All bands merged into one GeoJSON `FeatureCollection`, returned to frontend.
5. Frontend renders it as a Leaflet layer on top of the FR1 satellite layer.

For the *visual* overlay (as opposed to the raw geometry), each band/region is colored by its own elevation
value using a colormap rather than one flat line color — see readability finding below.

### Verification

Tested end-to-end against a real Bhilai neighborhood (21.190°N, 81.348°E), starting from the plain satellite
image of that area (`fr2_region_satellite.png`) and deriving contours from real fetched elevation data —
not synthetic/sample data.

**Mechanics, traced through actual intermediate values** (`fr2_mechanics_labeled.png`):
- Raw API response: a 256×256 PNG, e.g. pixel `(0,0)` RGB `(129,43,50)` → decodes to `299.20 m`
- Thresholded at 300 m → binary mask
- `findContours` on that mask returned 31 separate contours; the largest was **391 pixel vertices**
- `approxPolyDP` simplified it to **88 vertices**, same shape
- Converted to lat/lon, e.g. pixel `(77,18)` → `lat=21.206018, lon=81.349382` — this is what actually lands
  in the GeoJSON sent to the frontend

**Readability finding (real problem, not cosmetic):** a first pass drawing every contour in one flat color
(yellow) was correct geometrically but unreadable — a single-color line only marks *a* boundary, it carries
no information about which side is higher. Fixed by coloring each contour/region by its own elevation value
(`turbo` colormap: blue/purple = low → red = high) and filling between contour lines with translucent color
bands (`fr2_colored_regions.png`). This is also more directly useful than plain lines for the app's actual
purpose — spotting low-lying candidate zones is a glance, not a trace.

**Cross-check against ground truth:** the computed low-elevation (blue) zone in the colored output spatially
matches the real stream channel visible in the plain satellite image of the same area — the winding
dark-green watercourse in `fr2_region_satellite.png` lines up with the blue band in `fr2_colored_regions.png`.
This confirms the derived elevation data reflects real terrain, not an artifact.

**Parameter tuning required, not a fixed constant:** initial dense-urban test (Bhilai city core, z14, 2 m
interval, no smoothing) produced 1108 tangled polylines — unusable. Smoothing + coarser interval (5 m)
brought it to 92 readable polylines. Interval/smoothing will likely need to scale with local relief and
terrain type (dense urban vs. open rural).

**Resolution ceiling found:** AWS Terrain Tiles return **HTTP 404 at zoom 16** — z15 (~4.45 m/px near
21°N) is the maximum available elevation resolution. Satellite imagery can zoom further (tested to z18,
~0.56 m/px), but elevation-derived contours cannot go past z15 regardless. This caps how tightly terrain
analysis can zoom in, independent of how detailed the imagery looks.

**Unresolved/flagged:** a cluster of unusually dense, tightly nested contours appeared near a lake edge in
two independent runs (different zoom levels, same general area) — consistent enough to suggest real steep
terrain (embankment) rather than a one-off artifact, but not conclusively confirmed. Flagged for a dedicated
check before relying on it in the actual candidate-scoring logic (FR3/FR4).

**Images saved:** `fr2_region_satellite.png` (plain satellite reference), `fr2_mechanics_labeled.png`
(threshold → findContours → approxPolyDP, labeled), `fr2_colored_regions.png` (final elevation-colored
overlay with contour boundaries), `fr2_zoomed_readable.png` (three-way comparison: satellite alone / flat
vs. colored lines / filled bands).

**Conclusion:** FR2 requires no new API beyond what FR1/terrain analysis already use — contour geometry is
entirely derived, via OpenCV, from the same elevation data the catchment analysis (FR4) will also consume.
The one design decision that matters going forward: render as colored filled regions, not flat-color lines.

---

## FR3 — Identify available land suitable for pond excavation

**API used:** none new. No open dataset of Indian government/available-land parcels exists (confirmed earlier
in the project). Reuses the **same elevation grid already fetched for FR2** — no additional API call needed
for the automatic half. **Overpass API** (OpenStreetMap building footprints, verified earlier: 212 buildings
returned for a real rural bbox) supplies the building-exclusion warning layer.

**Library used:**
- `numpy` / `scipy.ndimage` (`gaussian_filter`, `label`, `sum`) — depression detection and connected-component
  grouping
- `heapq` / `itertools` — priority-queue implementation of the fill algorithm (hand-rolled, not a GIS library)
- `cv2.findContours` / `approxPolyDP` — same contour-extraction machinery as FR2, reused here to get each
  candidate zone's boundary as a polygon
- Leaflet-Geoman (frontend) — freehand/polygon drawing for the human-confirmation half

**The algorithm — Priority-Flood:** a standard, published hydrology/GIS algorithm (Barnes, Lehman & Mulla,
2014), not something built for this project. It's the same depression-filling step used internally by
ArcGIS's `Fill`, GRASS `r.fill.dir`, TauDEM's `PitRemove`, and `richdem`/`pysheds`. Structurally it's
Dijkstra's algorithm applied to elevation instead of distance: seed a priority queue with the border cells,
always expand the lowest-elevation resolved-adjacent cell next, and set each newly-resolved cell's output
height to `max(its own elevation, the elevation it was reached at)`. That guarantees every interior cell has
a non-decreasing path back out to the map edge — i.e. no more dead-end pits. Runs in O(N log N).

Its textbook purpose is **DEM conditioning** — removing noise-induced pits so flow-routing algorithms (FR4)
don't get stuck in them. We reuse its *byproduct* — `depression_depth = filled_DEM − original_DEM` — as the
pond-candidate signal: any cell with positive depth is somewhere water would naturally pool.

**Wiring:**
1. Take the (already-fetched, FR2) elevation grid, run Priority-Flood → filled grid.
2. `depth = filled − original`. Threshold (e.g. `depth > 0.3m`) → binary candidate mask.
3. `scipy.ndimage.label` → connected-component labeling, splitting the mask into distinct numbered zones.
4. Per zone: pixel count → area (m²/ha) via ground resolution; max/mean depth; `cv2.findContours` → boundary
   → `approxPolyDP` → simplified polygon → pixel-to-latlon → centroid; perimeter → compactness
   (`4π×area/perimeter²`).
5. Each zone becomes one GeoJSON `Feature` (polygon geometry + properties above); the full set is a
   `FeatureCollection` sent to the frontend and rendered as a ranked, colored overlay on the satellite/contour
   map — same rendering approach as FR2's colored regions.
6. Overpass building footprints drawn as a separate warning layer on top (not a hard filter — see the
   project's earlier design decision to replace "detect government land" with human judgment).
7. **Human confirmation:** the user draws a polygon on the map, at/near whichever candidate looks right. That
   drawn polygon — not the automatic suggestion alone — is what FR4 onward actually operates on.

### Verification

Ran end-to-end on the real Bhilai region (21.190°N, 81.348°E, same elevation grid as FR2's test).

```
depression cells (depth > 0.3m): 135,274
connected depression zones found: 245
zones surviving area >= 200 m2 filter: 236
```

Example single candidate record, exactly as it would be returned to the frontend:
```json
{
  "candidate_id": 38, "area_ha": 14.704, "max_depth_m": 9.03, "mean_depth_m": 2.79,
  "perimeter_m": 2315.7, "compactness": 0.345,
  "centroid": {"lat": 21.202401, "lon": 81.341367},
  "geometry": { "type": "Polygon", "coordinates": [[ ...36 [lon,lat] vertices... ]] }
}
```

**Real flaw found, not yet fixed:** ranking candidates by raw area alone put the **stream corridor itself**
at the top of the list (largest zone, 38 ha) — visually confirmed against the satellite image, it traced the
entire watercourse rather than a discrete bowl. Priority-Flood correctly identifies a river valley as "one
connected depression," but a live drainage channel is not a pond site. **Fix identified but not yet
implemented:** filter/downrank by `compactness` — a stream corridor is long and thin (low compactness), a
genuine pond bowl is roughly round (compactness closer to 1.0). Needs to be added and re-verified before this
ranking logic is trusted.

**Conclusion:** FR3's automatic half is a genuine, working depression-detection pass built entirely on data
already fetched for FR2 (no new API cost), using a standard published algorithm rather than a custom
heuristic. Its output is directly usable as a Leaflet overlay. The compactness fix is the one open item before
this ranking can be relied on as-is.

---

## FR4 — Estimate the catchment area contributing runoff

**API search result: no suitable API exists at the resolution this needs.** Checked three real candidates:

- **mghydro.com Global Watersheds API** — free, keyless, genuinely tested live and working:
  `GET https://mghydro.com/app/watershed_api?lat=21.202401&lng=81.341367&precision=high` → HTTP 200 in 2.2s,
  returned a real GeoJSON watershed polygon with `area_km2` already computed. **Rejected for wrong scale**:
  it runs on MERIT-Hydro, a global ~90m-resolution dataset built for river-basin-scale hydrology. For the
  exact point tested here it returned **37 km²** — two to three orders of magnitude larger than a village
  pond's actual catchment (which we're computing in hectares). At 90m/pixel a single farm-scale depression
  isn't even resolvable; the API snaps to the nearest *major* drainage line, not the local micro-catchment.
- **India-WRIS** — confirmed river-basin/major-watershed scale only ("catchment area boundaries from WRIS are
  available only for large river basins"). Rejected, same reason.
- **Esri ArcGIS REST Watershed service** — exists, but is a paid ArcGIS Online utility service consuming
  service credits, not a free/keyless API. Not pursued.

**Conclusion: the project's own hand-rolled D8 + flow-accumulation logic is the only viable option at
village-pond scale.** No free service operates at the meter-level resolution this needs — reuses the same
elevation grid already fetched for FR2/FR3, no new API call.

**Library used:** same as FR3 — `numpy`/`scipy` for the priority-flood fill, hand-rolled D8 flow-direction
and flow-accumulation (no pysheds/richdem).

**Input:** the user's drawn polygon (from FR3's human-confirmation step) + the elevation grid.

**Wiring:**
1. Priority-flood fill **with the epsilon gradient** (not the plain version used for FR3's depth map) — flat
   filled depressions otherwise have no resolvable downhill direction, which breaks flow routing.
2. **D8 flow direction** over the whole grid: each cell points to whichever of its 8 neighbors is the
   steepest drop.
3. **Flow accumulation** over the whole grid: process cells highest-to-lowest elevation; each cell passes its
   running upstream-area count to whichever neighbor it points to.
4. **Pour point** = the cell with the **highest accumulation value among cells inside the user's polygon**
   (see verification below for why this, and not "lowest elevation," is the correct choice).
5. **Catchment** = flood-fill upstream from the pour point, following the flow-direction arrows in reverse.
6. Catchment area = cell count × ground resolution².

### Verification

Tested on a real, specific case: FR3's candidate #38 (14.70 ha, Bhilai test region) treated as if the user
had drawn that polygon.

**Bug found and fixed — pour point selection.** First attempt picked the pour point as the lowest-elevation
pixel *inside* the polygon (seemed intuitive). Result: a catchment of only 70 cells (0.14 ha) — smaller than
the 14.7 ha pond itself, and only 57 of the polygon's 7,411 cells even overlapped the catchment. Diagnosed
why: flow direction is computed on the **filled** surface, and filling specifically removes the original
deepest point as a sink (that's its entire purpose — see FR3). So that point is no longer where flow
converges; measuring "what flows into the deepest point" measures almost nothing.

**Fix:** pour point = highest flow-accumulation value *inside* the polygon — i.e. the pixel through which
the most upstream water is already passing, which is the polygon's actual hydrological outlet. After the
fix: **94% of the polygon's own cells fell inside its own catchment** (up from ~0%), which is the correct
sanity check — a pond's own footprint should almost entirely be part of what drains into it.

**Open caveat, not yet resolved:** the corrected run returned 604.53 ha of catchment for the 14.7 ha pond
(41× ratio) — implausibly large. Ruled out stream-corridor contamination (the pour point sits 936m from the
nearest stream-corridor pixel found in FR3). Likely cause: **D8 is a known-unreliable algorithm on flat,
low-relief terrain**, and this Bhilai test tile only spans 32m of elevation across its entire 3.4km extent —
tiny noise-scale bumps (including building-induced DEM noise, flagged back in FR2) end up dictating flow
direction, and D8's single-direction rule can artificially funnel large flat areas toward one arbitrary
point. This is a documented limitation of D8 specifically (multi-flow-direction algorithms exist to address
it), not a new bug in this implementation. **Not yet re-verified against a higher-relief rural DEM** (e.g.
the project's earlier Wardha test area) to confirm the absolute catchment magnitude is trustworthy outside
this flat urban stress-test case — flagged as an open item before this number should be relied on.

**Conclusion:** the pour-point logic is now structurally correct (verified via the self-overlap check), no
external API can substitute at this resolution, and the remaining open question is validating absolute
catchment magnitude on genuinely rural (higher-relief) terrain rather than this urban stress test.

---

## FR5 — Query historical rainfall data using publicly available APIs

**API used:** Open-Meteo Archive API (`archive-api.open-meteo.com/v1/archive`) — free, keyless, daily
historical precipitation by lat/lon.

**Library used:** just `requests`. No processing library needed — the API returns clean JSON directly, no
decoding/geometry step like FR2–FR4 required.

**Input:** lat/lon of the user's chosen pond location (or its catchment centroid, from FR3/FR4) — reuses
coordinates already in hand, no new geocoding step.

**Wiring:**
1. Backend calls Open-Meteo Archive with the pond's lat/lon and a multi-year date range (e.g. last ~20
   years), requesting `daily=precipitation_sum`.
2. From the returned daily series, compute two figures: **annual mean total** (baseline) and **max single-day
   value** (design-storm figure — needed because sizing off the annual total badly oversizes a pond, a real
   flaw already found and documented for FR8 earlier in this project).
3. Cache the series per region — rainfall data doesn't change fast, no need to refetch per request.
4. Return both stats to the frontend for FR8's results overlay.

### Verification

Tested earlier in this project against a real rural Indian point (Wardha, Maharashtra, 20.75°N, 78.60°E),
full calendar year 2020:
```
HTTP 200, 6,913 bytes
days returned: 366 / 366 (zero gaps)
annual total: 1,160.9 mm
max single-day: 87.4 mm
elevation cross-check returned by the API: 289.0 m
```
Zero gaps in the daily series confirms the archive has no missing-data issue for rural Indian coordinates.
The max-single-day figure (87.4mm) is the number that FR6/FR7 should actually size against, not the 1,160.9mm
annual total.

**Conclusion:** FR5 is the simplest FR in the pipeline — one API call, no derived geometry, reuses coordinates
already computed by earlier FRs. Not yet re-run specifically against the Bhilai test area used for FR1–FR4;
mechanism and reliability already confirmed, so re-running is a formality rather than open verification work.

---

## FR6 — Estimate runoff volume using rainfall and catchment information

**API used:** none — pure computation using outputs already produced by FR4 and FR5.

**Library used:** none beyond plain arithmetic. No numpy/scipy needed — this is a single formula, not a grid
operation.

**Input:** catchment area (FR4's output, in m²) + rainfall depth (FR5's output).

**Formula — Rational Method:**
```
V = A × P × C
```
- `A` = catchment area (FR4)
- `P` = rainfall depth (FR5) — **must be the design-storm value (max single-day), not the annual total**
- `C` = runoff coefficient, fraction of rainfall that becomes surface runoff rather than infiltrating/
  evaporating (typically ~0.3 for mixed rural terrain — needs a citable source, not an assumed constant;
  flagged as an open item, same as FR7's depth figure)

**Why the design-storm distinction matters — already found the hard way:** the very first exploration pass
in this project computed `V` using **annual** rainfall against a large auto-picked catchment and got a
1,061m × 1,061m "pond" — obviously wrong. A pond fills and drains repeatedly across a season; it never holds
a full year of rain at once. Fixed by using FR5's max-single-day figure instead.

**Wiring:**
1. Take catchment area from FR4 (m²)
2. Take max-single-day rainfall from FR5 (convert mm → m)
3. Apply runoff coefficient C
4. `V = A × P × C` → runoff volume in m³
5. Pass V to FR7

**Open item carried over from FR4:** FR4's catchment number is not yet trustworthy on the flat urban Bhilai
test terrain (604 ha result, not yet re-verified against higher-relief rural terrain). Any volume computed
from it right now inherits that same uncertainty — FR6's formula itself is simple and correct, but its input
still needs validating before the output volume can be trusted end-to-end.

---

## FR7 — Recommend an appropriate pond depth and approximate storage capacity

**API used:** none — computation only, same as FR6.

**Library used:** none — plain arithmetic.

**Input:** runoff volume `V` from FR6, and the **pond footprint area from FR3** — the user's actual drawn
polygon, not a derived value.

**Key design decision:** don't solve for area from volume. That's the naive approach that failed in the
project's first exploration pass (see FR6) — "how big must the pond be to hold all the runoff" produces
absurd dimensions. Instead:

1. **Depth is recommended from a standard, not calculated** — a fixed practical range (2–3m), based on
   typical Indian farm-pond excavation practice. Needs a citable source (MGNREGA/ICAR guidelines) — flagged
   as an open item, same status as FR6's runoff coefficient.
2. **Area is whatever the user already drew in FR3** — fixed input, not solved for.
3. **Capacity = pond area × recommended depth.**
4. **Compare capacity against FR6's volume:** `capture_fraction = min(1, capacity / V)`. If capacity ≫ V the
   pond comfortably handles the design storm; if capacity ≪ V, that's a real signal back to the user — draw a
   bigger polygon in FR3, or accept partial capture in a large storm.

**Actual output:** recommended depth (from the standard range), resulting storage capacity in m³, and —the
more useful figure for a village administrator than either alone— **what percentage of a design-storm event
this specific pond, at this specific footprint, would actually capture.** That reframing (capture percentage
instead of a derived footprint) is what avoids the absurd-dimensions failure mode.

**Conclusion (FR6+FR7 together):** both are pure arithmetic with no new API or library dependency — they
consume FR3/FR4/FR5's outputs directly. The one substantive design choice that had to be corrected (design
storm vs. annual rainfall; fixed depth/area vs. solved footprint) is already made and reasoned through; what
remains open is citing real sources for C and the depth range, and validating FR4's catchment magnitude on
rural terrain before trusting the numbers these two FRs would produce end-to-end.

---

## FR7 — Recommend an appropriate pond depth and approximate storage capacity

_(not yet worked through)_

---

## FR8 — Overlay all results

_(not yet worked through)_
