# Setup

How to install, run, build and test the project locally.

---

## Requirements

- **Python 3.12**
- **Node.js 18+** and npm
- A **PostgreSQL connection string** (the project is built against a hosted
  [Neon](https://neon.tech) serverless Postgres, but any Postgres works). It's used to cache
  elevation tiles, satellite land-cover images, rainfall series and analysis results, so repeat
  requests for the same area don't re-fetch or recompute them.

---

## First-time setup

### Backend

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env      # then edit .env and set DATABASE_URL
```

`backend/.env` (see `backend/app/core/config.py` for the full list):

```bash
# Neon (or any Postgres) connection string
DATABASE_URL=postgresql://<user>:<password>@<host>/<dbname>?sslmode=require

# Allowed frontend origin(s) for CORS, comma-separated
CORS_ORIGINS=http://localhost:5500,http://127.0.0.1:5500

# Zoom level for elevation tile fetches (14 ≈ 8.9 m/px at Chhattisgarh's latitude)
DEFAULT_ELEVATION_ZOOM=14
```

The database schema (tables + indexes) is created automatically on backend startup — no migration
step needed.

### Frontend

```bash
cd frontend
npm install
```

---

## Run everything together

```bash
./run.sh
```

This checks the setup above is in place, then starts both processes and prints:

```
Frontend: http://127.0.0.1:5500   ← open this
Backend:  http://127.0.0.1:8000   (interactive API docs at /docs)
```

**Open the frontend URL, not the backend one.** The frontend's dev server proxies `/api/*`
requests straight to the backend (see `frontend/vite.config.js`), so there's nothing else to
configure — no CORS setup, no manually pointing the frontend at a backend address.

Ctrl+C stops both processes together.

## Run the pieces separately

```bash
# terminal 1 — backend
cd backend && source .venv/bin/activate
uvicorn app.main:app --reload --port 8000

# terminal 2 — frontend
cd frontend && npm run dev
```

Check the backend is alive:
```bash
curl http://127.0.0.1:8000/api/health       # {"status":"ok"}
curl http://127.0.0.1:8000/api/health/db    # {"status":"ok"}  (confirms Postgres is reachable)
```

---

## Production build

```bash
cd frontend
npm run build      # → frontend/dist/ (static files)
npm run preview    # serve dist/ locally on :5500, same /api proxy — a final sanity check
```

`dist/` is plain static files. Serve it from any web server that also forwards `/api/*` to the
backend (same origin), and the app works unchanged. To point a build at a backend on a different
origin, set `VITE_API_BASE` at build time (and add that origin to the backend's `CORS_ORIGINS`).

| Variable | When it applies | Default | Purpose |
|---|---|---|---|
| `VITE_API_BASE` | build time | `/api` | The API base URL baked into the build |
| `VITE_API_PROXY` | `npm run dev` / `npm run preview` | `http://127.0.0.1:8000` | Where the dev/preview server forwards `/api/*` |

---

## Testing

```bash
cd backend
pytest -m "not slow"   # offline — no network calls beyond what's already mocked; fast
pytest                 # everything, including tests that hit real external APIs; slower
```

See [BACKEND_GUIDE.md § Testing](BACKEND_GUIDE.md#testing) for what each test file actually covers.

There's no frontend test suite; verification of the frontend has been manual, through the browser.

---

## Trying it out

1. Open the frontend, zoom into an area of rural India (Chhattisgarh is the region this pipeline
   has been most tested against — village-scale, a few km² at a time works best).
2. Draw a rectangle or polygon with the tools in the top-left of the map, or search for a place
   name first to jump there.
3. Click **Find Pond Sites**. This takes anywhere from a few seconds to ~30s the first time for a
   given area (elevation tiles, water screening and rainfall are all fetched fresh); repeat runs
   over the same area are cached and much faster.
4. Click a ranked pond to see its catchment and full details.

To try the standalone contour-upload endpoint instead:
```bash
curl -X POST http://127.0.0.1:8000/api/analyzeContour \
     -F "contour_map=@backend/data/contours_1m.kml"
```
