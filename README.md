# Rural Pond Catchment Analysis

A web app that finds good places to dig rainwater-harvesting ponds in rural India, using nothing
but public satellite/terrain data. Draw an area on a map (or upload a contour map), and it returns
ranked pond locations, each with its catchment (the land whose rain drains into it) and the volume
of water it can be expected to collect in one heavy storm.

Built for a village-pond-planning assignment, in three phases — see [Project history](#project-history).

---

## What it does

1. **Pick an area** — draw a rectangle or polygon on the map, or search a place name.
2. **The backend analyzes the terrain** inside that area:
   - fetches elevation data and finds natural low-lying hollows deep and wide enough to hold water,
   - screens out anything that's already a river, tank or reservoir (satellite + OpenStreetMap),
   - traces the catchment — all the upstream land whose rainfall drains into each hollow,
   - sizes each pond against real historical rainfall (the Rational Method) and ranks sites by how
     much water they'd actually collect.
3. **Results are shown on the map** — pond locations as ranked pins; click one to see its catchment
   area, capacity, expected water volume, and any warnings (e.g. "this drains too large an area to
   be a simple farm pond").

A second, independent pipeline (`POST /api/analyzeContour`) does the same analysis from an
**uploaded KML/KMZ contour map** instead of live elevation tiles — this was the Phase 2 deliverable
and still works standalone.

For the full documentation set, see **[docs/](docs/)**:

| Document | What's in it |
|---|---|
| [`docs/SETUP.md`](docs/SETUP.md) | Install, run, build, test — start here to get it running |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | System diagram, both analysis pipelines end-to-end, design decisions |
| [`docs/API_REFERENCE.md`](docs/API_REFERENCE.md) | Every backend endpoint: params, response shape, error codes |
| [`docs/BACKEND_GUIDE.md`](docs/BACKEND_GUIDE.md) | Every service module, the terrain algorithms, constants, caching |
| [`docs/FRONTEND_GUIDE.md`](docs/FRONTEND_GUIDE.md) | Component tree, state flow, MapView/Leaflet internals, styling |
| [`docs/design/HLD.md`](docs/design/HLD.md) | Original high-level design (Phase 1) — historical, see note at its top |
| [`docs/verification/Verification_Results.md`](docs/verification/Verification_Results.md) | Early data-source verification notes (Phase 1) |

---

## Quick start

```bash
./run.sh
```

This starts the backend (FastAPI, port 8000) and frontend (Vite dev server, port 5500) together,
and prints the URL to open. First-time setup (Python venv, `.env`, `npm install`) is required
first — see **[docs/SETUP.md](docs/SETUP.md)** for the full walkthrough.

```
Frontend: http://127.0.0.1:5500   ← open this
Backend:  http://127.0.0.1:8000   (interactive API docs at /docs)
```

---

## Tech stack

| Layer | Choice | Why |
|---|---|---|
| Frontend | React + Vite, Leaflet + Leaflet-Geoman | Leaflet-Geoman gives free-draw rectangle/polygon tools out of the box |
| Backend | FastAPI (async Python) | Most endpoints wait on external HTTP calls (DEM tiles, WorldCover, rainfall) — async fits naturally; OpenAPI docs come for free at `/docs` |
| Terrain engine | numpy + scipy + OpenCV, hand-written | No GDAL/rasterio/pysheds dependency — elevation arrives as a PNG, the whole engine (depression fill, D8 flow routing, contour tracing) is plain array code, kept pip-installable |
| Database | PostgreSQL (Neon serverless) | Every DB access is a single-key JSON(B) cache lookup — no in-database spatial queries, so JSONB is enough; all real geometry work happens in Python |
| Elevation | AWS Terrarium tiles (free, keyless) | PNG-encoded, no GDAL needed to decode |
| Land cover / water | ESA WorldCover + Sentinel-2 SWIR (via a WMS) | Screens out existing ponds/rivers before ranking |
| Rainfall | Open-Meteo Archive (free, keyless) | Historical daily precipitation → the design storm used for sizing |
| Buildings / mapped water | Overpass API (OpenStreetMap) | A second, independent water-body signal, plus a "don't flood this building" warning layer |
| Place search | Nominatim (OpenStreetMap), India only | Jump the map to a named place |

## Repository layout

```
Rural_Pond_Catchment_Analysis/
├── run.sh                      # start backend + frontend together
├── Phase2_Report.pdf           # Phase 2 submission report
├── backend/
│   ├── app/
│   │   ├── main.py             # FastAPI app, router registration, CORS, lifespan
│   │   ├── core/config.py      # Settings (.env-backed)
│   │   ├── db/postgres.py      # asyncpg pool + schema + cache tables
│   │   ├── routers/            # HTTP endpoints — one file per feature area
│   │   └── services/           # the actual algorithms (terrain, sizing, parsing, external APIs)
│   ├── data/                   # sample KML + a verification image
│   ├── scripts/                # one-off DB seeding + standalone verification tools
│   └── tests/                  # pytest suite (see docs/SETUP.md for fast vs slow)
├── frontend/
│   └── src/
│       ├── App.jsx              # top-level state + the "Find Pond Sites" run lifecycle
│       ├── api.js               # every backend call, in one file
│       ├── format.js            # shared number/unit formatting
│       ├── geo.js / geocode.js  # client-side geometry + place search
│       └── components/          # sidebar panels + MapView (all Leaflet logic)
└── docs/                        # everything listed in the table above
```

## Project history

The assignment ran in three phases, each building on the last:

- **Phase 1** — initial design and admin-boundary/region-selection groundwork
  (`docs/design/HLD.md`).
- **Phase 2** — the standalone `POST /api/analyzeContour` endpoint: upload a KML/KMZ contour map,
  get back a ranked pond site with its catchment (`Phase2_Report.pdf`).
- **Phase 3** — the full interactive app: draw an area on a live map instead of uploading a file,
  see every result (pond, catchment, water volume) overlaid on the map itself, plus the UI,
  performance and deployment work that phase required.

Git history is the record of what changed and why at each step; task/requirement-tracking files
used during the work are not kept in the repo.
