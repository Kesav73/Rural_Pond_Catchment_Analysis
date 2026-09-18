# Frontend Guide

The component tree, how state flows through a "Find Pond Sites" run, MapView's Leaflet internals,
and the styling system. For the backend it talks to, see [API_REFERENCE.md](API_REFERENCE.md).

---

## Contents

- [Component tree](#component-tree)
- [State and the run lifecycle](#state-and-the-run-lifecycle)
- [Every component](#every-component)
- [MapView internals](#mapview-internals)
- [Shared helpers: `api.js`, `format.js`, `geo.js`, `geocode.js`](#shared-helpers)
- [Styling system](#styling-system)
- [`run.sh`](#runsh)

---

## Component tree

```
<App>                                    src/App.jsx — owns all state, see below
 └── #app
      ├── <aside id="sidebar">
      │     ├── <Brand/>                          static header
      │     ├── <RegionDrawPanel>                  "Step 1" — draw/search a region
      │     │     └── <PlaceSearch onPick/>        Nominatim search box
      │     ├── <AnalysisPanel>                    "Step 2" — contour mode + Find Pond Sites
      │     ├── <ProgressSteps>          (if a run has started)
      │     ├── <ErrorPanel>             (if the run failed)
      │     ├── <DataWarningBanner>      (if a data source was unavailable)
      │     ├── <NoSitesPanel>           (if the run found zero sites)
      │     ├── <ResultsPanel>           (if there are results, or one is loading)
      │     └── <footer>                           caveat text
      └── <MapView ref={mapRef}>                    all Leaflet logic — see below
```

Every sidebar panel is a thin, mostly-presentational component; **App.jsx owns all real state**
and **MapView owns all Leaflet state** — the two talk to each other only through props/callbacks
(`onRegionChange`, `onSelectSite`) and an imperative ref (see below), never by reaching into each
other's internals.

---

## State and the run lifecycle

State owned by `App` (`src/App.jsx`):

| State | Shape | Set by |
|---|---|---|
| `region` | `{bbox, polygon, areaKm2, overLimit}` \| `null` | `MapView`'s `onRegionChange` callback — drawing/editing/clearing a region on the map is the source of truth |
| `contourMode` | `"lines"` \| `"bands"` \| `"off"` | the segmented control in `AnalysisPanel` |
| `running` | bool | true for the duration of a "Find Pond Sites" run |
| `steps` | `null` until first run, else `{sites, catchments, contours}` each `pending`/`active`/`done`/`error`/`skipped` | the run itself, at each stage |
| `result` | `null` \| `{features, summary, outsideShape}` | the region-clipped candidates response |
| `catchments` | `{}` \| `{rank: catchmentResult}` | the `/api/catchment` response |
| `error` | string \| `null` | a failed run |
| `selectedRank` | number \| `null` | which pond's details are open |

Plus two refs used purely to cancel stale work: `runRef` (`{id, controller}` — bumped and aborted
on every region change or re-run) and a second, independent `contourRequest` controller (contours
load in parallel with candidates, so they get their own cancellation lifecycle).

### The "Find Pond Sites" click, step by step

1. `cancelRun()` — abort whatever the previous run was doing; bump the run id.
2. Reset the map (`mapRef.current.clearResults()`) and sidebar state; set `running=true`.
3. Fire `loadContours(contourMode, run)` **without awaiting it** — contours and candidates load
   concurrently, not one after the other.
4. `await api.fetchCandidates(bbox, 5, {signal})`. On success:
   - Clip both `features` and `excluded` to the drawn shape if it's a freehand polygon (a
     rectangle's bbox already *is* its shape, so no clipping is needed there) — via
     `pointInPolygon` from `geo.js`.
   - Draw the ponds (`setCandidates`) and rejected hollows (`setExcluded`), auto-show the rejected
     layer only if there were zero surviving sites, and fit the map to the results.
5. If zero sites survived, stop here (skip the catchment/buildings fetches).
6. Fire-and-forget `api.fetchBuildings` — failures are swallowed; the `summary.overpass_available`
   flag already discloses whether this layer is present.
7. `await api.fetchCatchments(bbox, polygons, ranks, {signal})` for **all** top-N sites at once.
   On success, `mapRef.current.setCatchments(...)` — note this **builds** every site's catchment
   layer but only actually **displays** the one the user has selected (see
   [Catchment-only-on-select](#catchment-only-on-select) below).
   On failure, every site's popup is told the catchment failed rather than left hanging on
   "Delineating…" forever.

**Every fetch takes an `AbortSignal`.** If the region changes or the user clicks "Find Pond Sites"
again mid-run, the old run's id no longer matches `runRef.current.id`, so its callbacks become
no-ops the moment they'd otherwise touch state — a slow response can never overwrite a newer run's
results.

### Selecting a pond

`handleSelect(rank, {fly})` is the single entry point, called from two places: clicking a result
row (`fly: true` — the map flies to it) and clicking a pond directly on the map
(`onSelectSite` → `fly: false`, it's already in view). It sets `selectedRank` and calls
`mapRef.current.setSelected(rank, {fly})`. Closing a pond's popup round-trips back through
`onSelectSite(null)`, so "click elsewhere to deselect" and "close the popup to deselect" behave
the same way.

Export (`ResultsPanel`'s "Download results" button) is entirely client-side —
`exportResults.js`'s `downloadResultsGeoJSON` builds a GeoJSON file from state already in memory,
no network call.

---

## Every component

| Component | Props | Role |
|---|---|---|
| **`RegionDrawPanel`** | `region`, `onClear`, `onPlacePick` | "Step 1": embeds `PlaceSearch`; shows the drawn region's area/shape once one exists, an over-limit warning, and a "Clear region" button. Never touches Leaflet directly. |
| **`PlaceSearch`** | `onPick` | Search-on-submit (not autocomplete, per Nominatim's usage policy) box; a single match auto-picks, multiple matches show a results list. |
| **`AnalysisPanel`** | `enabled`, `running`, `contourMode`, `onContourMode`, `onRun` | "Step 2": the Lines/Shaded/Off contour toggle and the "Find Pond Sites" button (shows a spinner + "Analyzing…" while running). |
| **`ProgressSteps`** | `steps` | A live checklist (sites → catchments → contours) while a run is in flight. Renders `null` once everything finished cleanly — it only stays visible if something errored, so it doesn't linger after a successful run. |
| **`ErrorPanel`** | `message`, `onRetry` | A failed run: the server's own error message, plus a "Try again" button that just re-triggers the same run. |
| **`DataWarningBanner`** | `summary` | Lists which optional data sources (WorldCover, SWIR, rainfall) were unavailable for *this* run and what that means, so a degraded result is never silently presented as complete. |
| **`NoSitesPanel`** | `summary`, `outsideShape` | Shown when a run found zero sites — a breakdown of how many hollows were found and why each was rejected (existing water / too elongated / outside the drawn shape), plus suggestions. |
| **`ResultsPanel`** | `loading`, `features`, `summary`, `selectedRank`, `onSelect`, `onExport` | The ranked list: one row per pond (rank badge, "Pond #n", expected water volume), a skeleton placeholder while loading, and the GeoJSON export button. Auto-scrolls the selected row into view. |
| **`Brand`** | — | Static header (icon + title), no logic. |
| **`MapView`** | `onRegionChange`, `onSelectSite` (+ forwarded ref) | Everything Leaflet — see below. |

---

## MapView internals

`src/components/MapView.jsx` is the sole owner of all Leaflet state. It renders nothing but a
`<div id="map">`; every pane, layer, and control is built imperatively inside one mount-only
`useEffect`.

### Panes (stacking order, bottom → top)

```
contours (350) < excluded/rejected (380) < catchments (400) < ponds (420)
              < place-name labels (450, pointer-events: none)
              < Leaflet's built-in markerPane (600) — the rank pins
              < tooltips < popups
```

Catchments sit **below** ponds on purpose — a catchment is usually far larger than the pond it
feeds, so it frames the pond rather than burying it.

### Layer groups

Five persistent `L.layerGroup()`s (`contours`, `excluded`, `catchments`, `ponds`, `buildings`) are
created once and **cleared and refilled** on every run, rather than recreated — so the layer
control's checkboxes, and any layer a user has manually hidden, survive across runs.

### Basemaps and the auto-switch

Three base layers: `Streets` (OSM), `Satellite + labels` (Esri imagery + Esri transportation/place
reference layers rendered into the `labels` pane), and plain `Satellite`. Below zoom 12 the map
defaults to Streets (better for finding your area); at zoom ≥ 12 it auto-switches to
`Satellite + labels` (better for judging a site) — **until the user manually picks a basemap**,
after which their choice sticks and the auto-switch stops. The new layer is always added before
the old one is removed, so the map never flashes empty mid-switch.

### Geoman draw controls (opt-in mode)

The map is built with **Leaflet-Geoman in global opt-in mode** (`L.PM.setOptIn(true)`) — meaning
*no* layer gets edit/drag/remove handles unless explicitly flagged. Only rectangle and polygon draw
tools are exposed in the toolbar (the two shapes that can describe a region). When a shape is
drawn, that specific layer is opted in (`pmIgnore: false` + `L.PM.reInitLayer`) and adopted as
*the* region — exactly one at a time, any previous region is removed first. Reshaping or dragging
it afterward re-emits the region to `App`. **Result layers (ponds, catchments, contours, etc.) are
never opted in**, so Geoman's edit/drag/remove tools can never touch them, no matter what's on
screen.

### Rank markers

Each pond gets an `L.divIcon` pin — a coloured pill (colour = rank, via `colors.js`'s
`rankColor(rank)`) showing only the rank number, positioned to sit just above the pond with a
pointer down to it. Selecting a pond scales and highlights its pin. The pin deliberately doesn't
carry the water-volume number — that lives in the results list and the popup, keeping the map
itself uncluttered.

### Catchment-only-on-select

`setCatchments()` builds a catchment polygon + pour-point marker for **every** returned site up
front (from the `/api/catchment` response), but `refreshSite(rank)` — called on every selection
change — only actually **adds** a site's catchment layer to the map if that site is currently
selected, and **removes** it otherwise. This is the fix for a real usability bug found during
development: drawing every catchment at once made the (usually much larger) catchment areas read
as "the sites," burying the actual pond locations. Now the map's default view is just "here are the
ranked ponds," and a catchment appears only once you ask about a specific one — with an on-map
label ("Catchment of pond #1 · 803.3 ha — rain falling here drains into pond #1") so it's never
mistaken for the pond itself.

### Contours: lines vs. bands

`setContours(body, style)` renders either thin/major-weighted white polylines with periodic
elevation labels (`style="lines"`), or filled polygons coloured by a blue→tan→red elevation ramp
with no per-line labels (`style="bands"` — the legend shows a gradient bar instead). Both are drawn
into the `contours` pane, below every result layer.

### The imperative handle

Everything `App.jsx` can ask `MapView` to do, via `useImperativeHandle`:

| Method | Does |
|---|---|
| `flyToBounds(bounds)` | Used by place search — flies to a location, capped at zoom 15 |
| `clearRegion()` / `clearResults()` | Removes the drawn region / all result layers and resets selection |
| `setContours(body, style)` | Draws the visual contour layer |
| `setCandidates(body)` | Draws pond polygons + rank pins from a `/api/candidates` response |
| `setCatchments(results)` | Builds (but doesn't necessarily show) every site's catchment from `/api/catchment` |
| `setCatchmentError(message)` | Marks every site's catchment as failed |
| `setExcluded(excluded)` | Draws rejected hollows, each with a hover tooltip explaining why |
| `showExcluded(visible)` | Shows/hides the whole rejected-hollows layer |
| `setBuildings(body)` | Draws the OSM building warning layer |
| `setSelected(rank, {open, fly})` | Selects a pond: shows its catchment, opens its popup, optionally flies to it |
| `fitToResults({onlyIfUntouched})` | Fits the map to all current results; can be told to back off if the user has since panned/zoomed themselves |

---

## Shared helpers

### `api.js`
Every backend call in one place, each accepting an optional `AbortSignal`:
`fetchContours`, `fetchCandidates`, `fetchBuildings`, `fetchCatchments`. Base URL is
`import.meta.env.VITE_API_BASE ?? "/api"` — relative by default, so the same build works in dev
(Vite proxies `/api` to the backend) and in production (served from behind the same origin).

### `format.js`
Every number the UI shows goes through here, so units are never inconsistent between the map, the
results list and the popup: `m3()`, `lakh()` (only used ≥ 1 lakh — `"1.37 lakh m³"`), `km2()`,
`ha()`, `pct()`, `expectedVolume()` (the headline figure, with a fallback for cached responses that
predate the field), `catchmentHa()` (prefers the traced catchment's exact area once it exists, over
the ranking step's flow-accumulation estimate), `fillStatus()` (turns `fill_ratio` into a
"Fills in one storm" / "Fills to N%" / "Only N% full" badge). Numbers use plain international
grouping (`137,037`), not Indian lakh-style grouping — a deliberate choice so a number doesn't read
differently to different users.

### `geo.js`
Client-side geometry, **deliberately mirroring the backend's own limits** so the UI can reject an
oversized area before ever calling the API: `exceedsTileBudget()` reproduces `elevation.py`'s exact
tile-count formula (`MAX_TILES_PER_AXIS = 12` at zoom 14). Also: `ringAreaKm2`/`bboxAreaKm2` (for
the region-size readout) and `pointInPolygon` (used to clip results to a freehand-drawn polygon,
since the backend only knows about its bounding box).

### `geocode.js`
Place search via Nominatim, restricted to India (`countrycodes: "in"` — the only region the
elevation/water-cover/rainfall pipeline has been verified for). Search-on-submit only, per
Nominatim's usage policy (max 1 request/second, no autocomplete).

---

## Styling system

Every colour, spacing value, radius and font size in `styles.css` comes from CSS custom properties
declared once on `:root` — no component hardcodes a colour. Two palettes:

- **Dark surfaces** (the sidebar): `--bg`, `--surface`, `--surface-raised`, `--surface-sunken`,
  `--border(-strong)`, `--text(-muted/-faint)`.
- **Light surfaces** (map popups only): `--paper`, `--paper-text`, `--paper-muted`,
  `--paper-border` — Leaflet popups render as white cards regardless of the app's own theme, so the
  design leans into that rather than fighting it, instead of forcing a dark popup.

Plus: brand/status colours (`--accent`, `--good`, `--warn`, `--bad`), a real **disabled** state
(`--disabled-bg`/`--disabled-text` — a distinct grey, not just a faded accent colour, since a faded
green button still read as clickable), a spacing scale (`--space-1`…`--space-5`), radii
(`--radius-sm/md/lg`), and a type scale (`--text-xs`…`--text-xl`).

Per-rank colouring (used on result rows, map pins, and popup badges) is done with one shared CSS
rule referencing `var(--rank-color, var(--accent))`, with `--rank-color` set inline by JS per
element — so there are no rank-specific stylesheet rules to keep in sync.

---

## `run.sh`

What it actually does, in order:

1. Checks `backend/.venv` exists (prints setup instructions and exits if not — doesn't create it).
2. Checks `backend/.env` exists (same).
3. Checks `npm` is on PATH.
4. Runs `npm install` in `frontend/` only if `node_modules` is missing (one-time bootstrap).
5. Starts `uvicorn app.main:app --reload --port 8000` in the background.
6. Starts `npm run dev -- --host 127.0.0.1` (Vite, port 5500) in the background.
7. Prints both URLs and waits on both processes — Ctrl+C stops both together (via a trap on
   `EXIT INT TERM`).

The frontend's Vite dev server proxies `/api/*` straight to the backend
(`vite.config.js`'s `server.proxy`), so **only the frontend URL needs to be opened** — there's no
CORS configuration to think about in local development, and the frontend's own code never needs to
know the backend's actual host/port.
