# Requirements to Verify — Pond Placement System

Everything the system needs, stated as a question that must be answered YES/NO with evidence
before the HLD is written. Nothing here is assumed to work until tested.

Status legend: `[ ]` untested · `[OK]` verified working · `[PARTIAL]` works with limits · `[NO]` unusable

---

## R1 — Administrative data (State / District / Region dropdowns)

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R1.1 | List of all Indian states | Is there a free source/API returning all 28 states + 8 UTs? |
| R1.2 | Districts per state | Free source returning districts filtered by state? |
| R1.3 | Sub-district / village / region | Does a 3rd level exist openly? Or do we stop at district? |
| R1.4 | Boundary geometry | Do we get **polygons** (GeoJSON) or only names? Polygons needed to clip the DEM. |
| R1.5 | Centroid / bbox per region | Needed to centre the map and to request the right DEM tile. |

**Verify by:** find dataset, download, count records, inspect one district's geometry.

---

## R2 — Base map / satellite imagery

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R2.1 | Street/base tiles | Free tile URL usable in Leaflet without a key? |
| R2.2 | Satellite tiles | Free satellite imagery tiles? What zoom level over rural India? |
| R2.3 | Licence / usage policy | Are we allowed to use it for an academic demo? Tile-usage limits? |
| R2.4 | Raw imagery download | Do we need actual pixels (for OpenCV) or only display tiles? |

**Verify by:** request a real tile URL for a known rural lat/lon, confirm HTTP 200 + image bytes.

---

## R3 — Elevation / DEM (the core input)

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R3.1 | DEM source | Which free API returns an elevation raster for an arbitrary bbox? |
| R3.2 | Resolution | 30 m enough to find village-scale depressions? Is 10 m available? |
| R3.3 | API key | Required? Free tier limits? Signup friction? |
| R3.4 | Output format | GeoTIFF? Can we read it without rasterio/GDAL installed? |
| R3.5 | Point-elevation fallback | Is there a simple lat/lon → elevation JSON API if raster fails? |
| R3.6 | India coverage | Confirm real data returned for a rural Indian bbox, not voids/zeros. |

**Verify by:** actually download a DEM for a rural bbox, load it, print min/max/shape.

---

## R4 — Contour generation

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R4.1 | Is there a contour API? | Or must we derive contours from the DEM ourselves? |
| R4.2 | Derivation method | OpenCV `findContours` on thresholded elevation bands? matplotlib `contour`? |
| R4.3 | No-GDAL path | Can we generate contours with only numpy/opencv/scipy? |
| R4.4 | Output to map | Convert contour pixel coords → lat/lon GeoJSON for Leaflet. |

**Verify by:** run the chosen method on a downloaded DEM, produce contour lines, sanity-check count/shape.

---

## R5 — Terrain analysis (sinks, flow, catchment)

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R5.1 | Depression / sink detection | Algorithm available without pysheds/richdem? (scipy morphological fill?) |
| R5.2 | Flow direction (D8) | Implementable in numpy at acceptable speed for our raster size? |
| R5.3 | Flow accumulation | Same. |
| R5.4 | Catchment delineation | Upstream cells draining to a chosen pour point. |
| R5.5 | Performance | How long for a ~1000x1000 raster? Acceptable for a web request? |

**Verify by:** implement/prototype on the real DEM, time it, verify sinks land in visually low areas.

---

## R6 — Buildings / settlement exclusion

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R6.1 | Building footprints API | Overpass API free + no key? |
| R6.2 | Rural India coverage | Does a random Indian village actually have buildings mapped? |
| R6.3 | Rate limits | Overpass throttling — usable in a live request? |
| R6.4 | Fallback landcover | If OSM is empty, is there a built-up landcover raster? |

**Verify by:** query Overpass for a real village bbox, count returned buildings.

---

## R7 — Rainfall / precipitation

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R7.1 | Historical daily rainfall | Free API, no key, by lat/lon? |
| R7.2 | Length of record | Enough years to compute a reliable annual mean? |
| R7.3 | Design storm value | Can we get max-1-day rainfall / return-period value? |
| R7.4 | Official Indian source | Is IMD / data.gov.in usable, or is a global reanalysis acceptable? |

**Verify by:** call the API for a rural Indian lat/lon, get real mm values, compute annual mean.

---

## R8 — Pond sizing model

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R8.1 | Runoff formula | Rational method / SCS-CN — which, and what inputs does it need? |
| R8.2 | Runoff coefficient | Source for a defensible value for Indian rural terrain? |
| R8.3 | Depth assumption | Standard farm-pond depth in Indian practice — citable? |
| R8.4 | Evaporation / seepage | Include or explicitly out of scope? |

**Verify by:** find a citable reference (govt guideline / textbook), write the formula down.

---

## R9 — Frontend / interaction

| ID | Requirement | Question to answer |
|----|-------------|--------------------|
| R9.1 | Map library | Leaflet — free, works offline-ish, CDN available? |
| R9.2 | Polygon drawing | Leaflet-Geoman / Leaflet.draw — free tier does freehand + polygon? |
| R9.3 | Raster overlay | Can we overlay a generated PNG (sinks heatmap) georeferenced on Leaflet? |
| R9.4 | GeoJSON rendering | Contours + catchment + pond outline as GeoJSON layers. |

**Verify by:** confirm CDN URLs load, check plugin licence.

---

## R10 — Local environment

| ID | Requirement | Status |
|----|-------------|--------|
| R10.1 | Python | `[OK]` 3.13.4 |
| R10.2 | Flask | `[OK]` 3.1.3 |
| R10.3 | numpy / scipy / opencv / pillow / requests | `[OK]` present |
| R10.4 | rasterio / GDAL | `[ ]` NOT installed — can it be pip-installed on Windows? |
| R10.5 | pysheds / richdem | `[ ]` NOT installed — needed, or hand-roll in numpy? |
| R10.6 | geopandas / shapely | `[ ]` NOT installed — needed? |

**Verify by:** attempt install; if it fails on Windows, design around it.

---

## Open design questions (decide after verification)

- **Q1** — Do we stop the dropdown at district, or go to sub-district/village?
- **Q2** — Is the DEM fetched live per request, or pre-downloaded for a demo district?
- **Q3** — Does OpenCV do real work here (contour extraction, segmentation), or is it decorative?
- **Q4** — Fixed pond depth with computed area, or solve for both?
- **Q5** — Live API calls per request, or cache to disk?

---

## Verification results

_(filled in as each item is tested — see `Verification_Results.md`)_
