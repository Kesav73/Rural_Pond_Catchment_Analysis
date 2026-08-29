# Demonstration — `POST /api/analyzeContour`

Run against the provided sample contour map, `backend/data/contours_1m.kml` (6.7 MB).

## Request

```bash
curl -X POST http://127.0.0.1:8000/api/analyzeContour \
     -F "file=@backend/data/contours_1m.kml"
```

`HTTP 200` in **45.0 s**.

---

## What the service read from the file

Every one of these is parsed or measured, not configured:

| | |
|---|---|
| Contour lines | 1,355 |
| Elevation levels | 32, from 267.0 m to 298.0 m |
| Contour interval | **1.0 m** (measured as the modal gap between levels) |
| Elevation stored in | `<name>` (chosen automatically over Z / ExtendedData / description) |
| Vertices used | 159,113 |
| Extent | 81.2814–81.3126 E, 21.2398–21.2636 N |
| Boundary polygon | found, and applied to restrict proposals |
| Skipped placemarks | 0 |

The vertex count is worth checking: the file holds 160,473 vertices in total, of which 1,355 belong
to label `Point` placemarks and 5 to the boundary polygon. `160,473 − 1,355 − 5 = 159,113`. Label
geometry is excluded by geometry *type*, not by the folder it happens to sit in.

Derived parameters:

| | | Derived from |
|---|---|---|
| Grid resolution | 1.97 m/px (1345 × 1648) | measured contour spacing of 7.87 m (25th percentile) |
| Depth threshold | 1.00 m | one contour interval |
| Interpolated fraction | 97.5% | rest nearest-filled beyond the triangulation hull |

---

## Result — the proposed pond

```json
"pond_site": {
  "rank": 1,
  "centroid":     { "lat": 21.241027, "lon": 81.295568 },
  "area_ha":      1.842,
  "mean_depth_m": 2.730,
  "max_depth_m":  4.941,
  "compactness":  0.796
}
```

Compactness 0.796 on a 0–1 scale — a well-rounded bowl, not a channel.

## Result — its catchment

```json
"catchment": {
  "area_ha":                 44.11,
  "catchment_to_pond_ratio": 24.0,
  "pour_point":       { "lat": 21.241555, "lon": 81.295176 },
  "self_overlap_pct": 75.5
}
```

**75.5% self-overlap** means most of the pond drains to its own outlet, which is the check that the
delineation is trustworthy. The 24× ratio sits well below the 50× threshold at which a site is
really an in-stream structure, so no warning was raised.

## Sizing

| | |
|---|---|
| Design storm (max single-day rainfall) | 173.4 mm |
| Runoff from the catchment | 13,769 m³ |
| Recommended depth | 3.0 m |
| Pond capacity | 38,672 m³ |
| Captures | 100% of one storm's runoff |
| Fills to | 36% in a single design storm |

---

## Screening — what was rejected, and why

```
1019 depressions detected
  94 remained after the area filter and the file's boundary polygon
  26 excluded — on or beside existing water
   7 excluded — too elongated to be a pond
  61 eligible
   5 returned
```

Both satellite sources responded. **OpenStreetMap did not**: `osm_available` is `false`, because no
reachable Overpass mirror returns data for this area — one answers HTTP 200 with an empty database.
So this screen is satellite-only, and the response says so rather than implying otherwise.

Examples of rejections, quoted from the response:

```
id 309, 24.71 ha — "on or beside an existing water body (satellite water within 50 m 100%)"
id 980,  1.45 ha — "on or beside an existing water body (satellite water within 50 m 100%)"
id 423,  2.21 ha — "elongated (compactness 0.475 < 0.5) — likely an unmapped drainage line"
id 384,  5.50 ha — "elongated (compactness 0.337 < 0.5) — likely an unmapped drainage line"
```

The first is the important one. Candidate 309 is **24.7 ha — over thirteen times larger than the
site finally proposed** — and depression-filling is most confident exactly where water already
collects, so on raw terrain it would have been an attractive answer. It is 100% water: an existing
body, not a place to dig. This is why the water screen runs *before* ranking rather than after.

## Alternatives returned

| Rank | Pond area | Catchment | Ratio | Self-overlap |
|---|---|---|---|---|
| 1 | 1.84 ha | 44.1 ha | 24× | 76% |
| 2 | 0.94 ha | 38.2 ha | 41× | 93% |
| 3 | 0.81 ha | 34.9 ha | 43× | 80% |
| 4 | 0.48 ha | 104.2 ha | 218× | 82% |
| 5 | 1.67 ha | 29.2 ha | 17× | 41% |

Rank 4's 218× ratio marks a site on a drainage line; rank 5's 41% self-overlap marks a less reliable
delineation. Both are reported rather than hidden.

---

## Independent verification

Four checks that do not rely on the pipeline agreeing with itself.

### 1. The surface against independent satellite elevation

The sample covers ground that AWS Terrain Tiles also cover, so the contour-derived surface can be
compared against a completely separate elevation source over the same 90,000 points:

```
correlation             0.99500
mean difference (bias)  +0.048 m
RMSE after debiasing     0.532 m
```

A constant offset would have been acceptable (different source, different vertical datum); the near
-zero bias and 0.995 correlation say the georeferencing and interpolation are right.

### 2. The surface against its own contours

Sampling the generated grid *on* the contour lines themselves gives a mean error of **0.073 m**
against a 1.0 m contour interval; only 0.84% of samples exceed one interval. This check is
self-referential and so works for any uploaded map, not just this one.

### 3. The mask against satellite imagery

`backend/data/water_exclusion_check.png` — regenerate with
`python scripts/render_water_check.py data/contours_1m.kml`.

Left: raw satellite imagery. Right: the same with the computed water mask in red and the five
proposed sites outlined in green.

This is the only **non-circular** check of the water screening available without a ground survey.
`verify_water_exclusion.py` confirms no returned site overlaps the mask, but it uses the same mask
that did the excluding, so it establishes the wiring rather than the mask's accuracy. Comparing
against imagery is independent.

What it shows: the river is fully covered, visibly wider than the water itself — that is the 50 m
proximity buffer. The tanks in the north-east appear as distinct red blobs. All five proposals sit
on open farmland, clear of red.

The two satellite signals also agree with each other, and they are not the same measurement twice —
WorldCover is a trained land-cover classifier, SWIR is a raw reflectance threshold:

| | share of map |
|---|---|
| WorldCover class 80 | 8.07% |
| SWIR < 40 | 9.21% |
| union | 9.53% |
| union + 50 m buffer | **17.61%** (what actually excludes) |

96.0% of WorldCover water is also SWIR-dark; 84.1% of SWIR-dark is also WorldCover water.

### 4. Generality

The same endpoint was run on synthetic contour maps that differ from this sample in every respect it
could have been tuned to — 2 m interval, 40 m base elevation, elevation in the Z column /
`ExtendedData` / `<description>`, no XML namespace, KMZ container, no boundary polygon, southern
hemisphere at negative longitude. On an analytically-defined bowl the pipeline located the pond
centre to within **0.0012°** of its true position.
