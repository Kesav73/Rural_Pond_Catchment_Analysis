# Change: draw-a-region replaces village selection entirely

## Decision (resolved)

No state/district/village selection UI at all, in any form — not even as a "jump to" convenience.
The **only** way to select an analysis area is drawing a shape (rectangle or polygon) directly on
the map. This supersedes the earlier draft of this doc, which floated keeping the dropdowns as a
navigation aid — that option is off the table per instruction.

## Goal / UX flow

1. App loads showing the full-India map (already the default view — unchanged).
2. User pans/zooms manually (standard Leaflet controls, already free) to their area of interest.
3. User draws a rectangle or polygon directly on the map using the Geoman toolbar that already
   renders on the map (currently unwired — see below).
4. That drawn shape becomes "the region": the sidebar shows a live area readout for it.
5. "Show Contours" / "Find Pond Sites" are enabled **only** once a region is drawn (and it's within
   the size guardrail), and operate on that shape — its bbox for the elevation/candidate fetch, and
   its exact polygon (when non-rectangular) to clip the returned candidates afterward.
6. Drawing a new shape replaces the old one and clears prior results. An explicit "Clear region"
   control is also available, separate from Geoman's own delete icon.

## Removed

- `frontend/src/components/RegionPanel.jsx` — deleted. No replacement state/district/village UI.
- `frontend/src/components/Combobox.jsx` — deleted. Confirmed its only consumer is `RegionPanel`
  (grepped: no other `.jsx` file imports it), so nothing else breaks.
- `App.jsx`: the `regionEntered` boolean, the `onGo`/`handleGo` handler, and the `getBoundsBbox()`
  imperative method on `MapView` (confirmed its only two call sites are the two handlers being
  rewritten below) — all removed.
- Sidebar "Step 1: Choose a location" panel — gone. Numbering shifts (see below).

## Explicitly not touched

- Backend `/api/states`, `/api/districts`, `/api/villages` endpoints, their Postgres tables
  (`districts`, `villages`), and the seed scripts (`seed_districts.py`, `seed_villages.py`). Nothing
  calls them from the frontend after this change, but deleting a working data layer isn't required
  to ship this feature, and it's a reasonable base if a "search by place name" convenience is ever
  wanted later. Say so explicitly if you'd rather I remove these too — leaving unused backend code
  in place is a judgment call, not an oversight.
- `/api/analyzeContour` (Phase 2 KML upload endpoint) — unrelated pipeline, untouched.

## Added

### 1. Region drawing (`MapView.jsx`)

- `map.pm.addControls(...)` currently sets `drawRectangle: false` — flip it to `true`.
  `drawPolygon` is not in that disabled list today, so it's already rendered in the toolbar and
  simply has no listener wired to it — this change is what actually makes it do something.
  Marker/circle/circleMarker/polyline/text stay disabled; none of them describe "a region."
- On map init, listen for Geoman's `pm:create` event on the map:
  - Ignore any shape type other than Rectangle/Polygon (defensive — shouldn't fire given the
    controls above, but don't assume).
  - Remove any previously drawn region layer first. Exactly one active region at a time.
  - Store the new layer; attach `pm:edit` (reshape) and `pm:remove` (delete) listeners on that
    specific layer, so editing or deleting it after the initial draw keeps region state in sync,
    not just the first creation.
- On every create/edit, compute and report (via a new `onRegionChange` callback prop — see below):
  - `bbox`: the layer's `getBounds()` → `[minLon, minLat, maxLon, maxLat]`. This is what actually
    goes to `/api/contours` and `/api/candidates` — their contract doesn't change.
  - `polygon`: the drawn shape's raw GeoJSON geometry (`layer.toGeoJSON().geometry`) when it's a
    polygon; `null` for a rectangle (a rectangle's bbox already *is* its shape, nothing further to
    clip).
  - `areaKm2`: an approximate area estimate (planar/haversine is fine — this drives a sidebar
    readout and the guardrail below, not anything geometrically load-bearing).
  - `overLimit`: result of the size guardrail (next section).
- New imperative method on the `MapView` ref: `clearRegion()` — removes the drawn layer and resets
  internal state. Used by an explicit sidebar "Clear region" button, since relying on users to find
  Geoman's own trash icon is weaker discoverability for the primary way to reset.
- New prop: `onRegionChange(region | null)`, called with `{ bbox, polygon, areaKm2, overLimit }` on
  create/edit, and `null` on remove/clear.

### 2. Size guardrail (`MapView.jsx`, alongside the above)

- The backend hard-caps the elevation fetch at `MAX_TILES_PER_AXIS = 12` tiles per axis at
  `default_elevation_zoom` (14) and returns 400 past that (`elevation.py`; verified failing at
  "69×74 tiles → rejected" per `Tasks.md`). Free-form drawing on an India-wide map makes it trivial
  to draw something that big on a first attempt.
- Approximate the same check client-side: at zoom 14, one Web Mercator tile spans `360 / 2^14`
  degrees of longitude (uniform); use the same figure as a latitude approximation too — it only
  needs to be close enough to warn early, since the backend remains the actual authority and still
  enforces the real cap regardless. Compare the drawn bbox's span against `12 × tile-degrees`.
- Within budget: sidebar shows the drawn area's size as a plain readout (e.g. "≈ 14.2 km²").
- Over budget: sidebar shows a warning ("Area too large — draw something closer to the size of a
  village, not a district") and the analysis buttons stay disabled even though a region technically
  exists — the user hits this before a failed request, not after one.

### 3. Sidebar restructure

- Delete the old "Step 1: Choose a location" panel entirely.
- New `frontend/src/components/RegionDrawPanel.jsx` (presentational, state lives in `App.jsx`):
  short instruction ("Zoom in, then draw the boundary of the area you want analyzed"), the live
  area readout or guardrail warning, and a "Clear region" button wired to
  `mapRef.current.clearRegion()`.
- Renumber: Step 1 "Draw the area to analyze" (new panel) → Step 2 "Analyze terrain" (existing
  `AnalysisPanel`, unchanged except what gates its `disabled` props) → Step 3 "Ranked sites"
  (existing `ResultsPanel`, unchanged).
- `App.jsx`: a `region` state object (`{ bbox, polygon, areaKm2, overLimit } | null`) set by
  `onRegionChange` replaces `regionEntered`. `contoursEnabled` / `candidatesEnabled` become
  `region != null && !region.overLimit`. `handleShowContours` / `handleFindPondSites` build their
  bbox string from `region.bbox.join(",")`.

### 4. Polygon results clipping (client-side, no backend change)

A rectangle's bbox already *is* the selected shape. A drawn **polygon** is smaller than its own
bounding box, so without this step, "Find Pond Sites" would silently analyze the full rectangle
around the polygon rather than the polygon itself:

- After `/api/candidates` returns, if `region.polygon` is set, drop any feature whose `centroid`
  (already present on every candidate in the response) falls outside the polygon.
- Plain ray-casting point-in-polygon test, written directly in `frontend/src/geo.js` — no new
  dependency (turf.js, etc.) needed for one point-in-polygon check against one small polygon.
- Contour bands are **not** clipped — they render across the full bbox as background context.
  Clipping arbitrary filled polygons client-side is real work for a layer that's context, not the
  answer, matching how buildings/water layers are already treated elsewhere in this app (warning
  layers, never hard filters).

## Backend changes required

**None.** `/api/contours?bbox=` and `/api/candidates?bbox=` already accept exactly the bbox this
produces, regardless of whether it came from a map viewport or a drawn shape. The polygon clip
happens entirely client-side, after the response comes back.

**Optional, later, only if client-side clipping proves insufficient in practice** (e.g. candidates
computed from bbox-only data prove meaningfully different from what a true polygon-aware terrain
solve would find): move the clip server-side by extending `/api/candidates` to accept a boundary
polygon. That would need a POST variant (arbitrary polygon geometry doesn't belong in a query
string) and should reuse the boundary-clipping logic that already exists in `analyze_contour.py`'s
KML pipeline rather than duplicating it. Not planned now — revisit only if asked.

## Files touched

- **Delete:** `frontend/src/components/RegionPanel.jsx`, `frontend/src/components/Combobox.jsx`
- **New:** `frontend/src/components/RegionDrawPanel.jsx`, `frontend/src/geo.js` (point-in-polygon +
  area/tile-guardrail helpers, shared between `MapView.jsx` and `App.jsx`)
- **Rewritten:** `frontend/src/components/MapView.jsx` — draw controls, `pm:create`/`pm:edit`/
  `pm:remove` handling, guardrail calculation, `onRegionChange`, `clearRegion()`; `getBoundsBbox()`
  removed
- **Rewritten:** `frontend/src/App.jsx` — `region` state replaces `regionEntered`; wires the new
  panel; applies the polygon clip to candidates before they reach `ResultsPanel` /
  `MapView.setCandidates`
- **Untouched:** every backend file; `AnalysisPanel.jsx`; `ResultsPanel.jsx`; `StatusBar.jsx`;
  `Brand.jsx`; `colors.js`; `api.js`

## Manual verification plan

1. Load the app — confirm no state/district/village UI appears anywhere, only the draw instruction.
2. Draw a rectangle over the project's usual test area (Bhilai/Durg,
   `81.30,21.15,81.45,21.30`-ish) — confirm contours/candidates run against exactly that box.
3. Draw a non-rectangular polygon over the same area — confirm candidates outside the polygon (but
   still inside its bounding box) are absent from both the map and the results list; contours still
   render across the full bbox.
4. Resize a drawn shape via Geoman's edit handles — confirm the region, area readout, and guardrail
   update without requiring a fresh draw.
5. Delete the drawn shape both ways — Geoman's own trash icon, and separately the sidebar "Clear
   region" button — confirm both disable the analysis buttons and clear prior layers/results.
6. From the India-wide default view, draw something state-sized — confirm the guardrail blocks it
   client-side with a clear message, and the request never reaches the backend to fail as a raw 400.
7. Draw a second region after already having results on screen — confirm the first region's
   contour/candidate layers and sidebar results panel are fully replaced, not layered on top of.
