// Relative by default: in dev Vite proxies `/api` to the backend (vite.config.js), and in production
// the frontend is served from behind the same origin as the API. VITE_API_BASE overrides both.
const API_BASE = import.meta.env.VITE_API_BASE ?? "/api";

// FastAPI errors carry a `{"detail": "..."}` body; surface that message when there is one, and fall
// back to the bare status code when there isn't (e.g. a proxy error page).
async function getJsonWithDetail(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

// Every call takes an optional AbortSignal so a run for a region the user has since redrawn can be
// cancelled rather than left to finish and draw stale results.
export function fetchContours(bbox, interval, { style = "bands", signal } = {}) {
  const params = new URLSearchParams({ bbox, interval, style });
  return fetch(`${API_BASE}/contours?${params}`, { signal }).then(getJsonWithDetail);
}

export function fetchCandidates(bbox, topN, { signal } = {}) {
  const params = new URLSearchParams({ bbox, top_n: topN });
  return fetch(`${API_BASE}/candidates?${params}`, { signal }).then(getJsonWithDetail);
}

export function fetchBuildings(bbox, { signal } = {}) {
  const params = new URLSearchParams({ bbox });
  return fetch(`${API_BASE}/buildings?${params}`, { signal }).then(getJsonWithDetail);
}

export function fetchCatchments(bbox, polygons, ranks, { signal } = {}) {
  return fetch(`${API_BASE}/catchment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bbox, polygons, ranks }),
    signal,
  }).then(getJsonWithDetail);
}
