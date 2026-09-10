const API_BASE = "http://127.0.0.1:8000/api";

async function getJson(res) {
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

// Endpoints that return a structured 4xx body ({"detail": "..."}) surface that message; endpoints
// that don't (states/districts/villages/buildings — see the routers) fall back to a plain HTTP code.
async function getJsonWithDetail(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
  return body;
}

export function fetchStates() {
  return fetch(`${API_BASE}/states`).then(getJson);
}

export function fetchDistricts(stateName) {
  return fetch(`${API_BASE}/districts?state=${encodeURIComponent(stateName)}`).then(getJson);
}

export function fetchVillages(districtName) {
  return fetch(`${API_BASE}/villages?district=${encodeURIComponent(districtName)}`).then(getJson);
}

export function fetchContours(bbox, interval) {
  return fetch(`${API_BASE}/contours?bbox=${encodeURIComponent(bbox)}&interval=${interval}`).then(
    getJsonWithDetail
  );
}

export function fetchCandidates(bbox, topN) {
  return fetch(`${API_BASE}/candidates?bbox=${encodeURIComponent(bbox)}&top_n=${topN}`).then(
    getJsonWithDetail
  );
}

export function fetchBuildings(bbox) {
  return fetch(`${API_BASE}/buildings?bbox=${encodeURIComponent(bbox)}`).then(getJson);
}

export function fetchCatchments(bbox, polygons) {
  return fetch(`${API_BASE}/catchment`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bbox, polygons }),
  }).then(getJson);
}
