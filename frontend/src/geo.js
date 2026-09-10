// Geometry helpers for turning a drawn shape into an analysis region.
//
// The tile math below is a deliberate mirror of `backend/app/services/elevation.py`
// (`deg2tile` / `tile_range_for_bbox` / `MAX_TILES_PER_AXIS`). A guardrail that disagrees with the
// cap the backend actually enforces is worse than no guardrail — it would either block valid areas
// or let through ones that still 400 — so this reproduces the formula rather than approximating it.
// The backend stays the authority; this only exists to fail fast in the UI.

const TILE_ZOOM = 14; // settings.default_elevation_zoom
const MAX_TILES_PER_AXIS = 12;

const KM_PER_DEG_LAT = 110.574;
const KM_PER_DEG_LON_EQUATOR = 111.32;

function deg2tile(latDeg, lonDeg, zoom) {
  const latRad = (latDeg * Math.PI) / 180;
  const n = 2 ** zoom;
  const xtile = ((lonDeg + 180) / 360) * n;
  const ytile = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return [xtile, ytile];
}

export function tileCounts([minLon, minLat, maxLon, maxLat]) {
  const [x1, y1] = deg2tile(maxLat, minLon, TILE_ZOOM); // NW corner (y grows southward)
  const [x2, y2] = deg2tile(minLat, maxLon, TILE_ZOOM); // SE corner
  const xmin = Math.floor(Math.min(x1, x2));
  const xmax = Math.floor(Math.max(x1, x2));
  const ymin = Math.floor(Math.min(y1, y2));
  const ymax = Math.floor(Math.max(y1, y2));
  return { nx: xmax - xmin + 1, ny: ymax - ymin + 1, max: MAX_TILES_PER_AXIS };
}

export function exceedsTileBudget(bbox) {
  const { nx, ny, max } = tileCounts(bbox);
  return nx > max || ny > max;
}

// Equirectangular projection around the ring's own mean latitude, then the shoelace formula. Good
// to a fraction of a percent at village scale, which is all a readout and a size warning need.
export function ringAreaKm2(ring) {
  if (!ring || ring.length < 3) return 0;
  const latMean = ring.reduce((sum, p) => sum + p[1], 0) / ring.length;
  const kmPerDegLon = KM_PER_DEG_LON_EQUATOR * Math.cos((latMean * Math.PI) / 180);
  let sum = 0;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const xi = ring[i][0] * kmPerDegLon;
    const yi = ring[i][1] * KM_PER_DEG_LAT;
    const xj = ring[j][0] * kmPerDegLon;
    const yj = ring[j][1] * KM_PER_DEG_LAT;
    sum += xj * yi - xi * yj;
  }
  return Math.abs(sum) / 2;
}

export function bboxAreaKm2([minLon, minLat, maxLon, maxLat]) {
  return ringAreaKm2([
    [minLon, minLat],
    [maxLon, minLat],
    [maxLon, maxLat],
    [minLon, maxLat],
  ]);
}

// Standard ray-casting test. One point against one small ring — not worth a turf.js dependency.
function pointInRing([lon, lat], ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i];
    const [xj, yj] = ring[j];
    const crosses = yi > lat !== yj > lat && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi;
    if (crosses) inside = !inside;
  }
  return inside;
}

// `geometry` is a GeoJSON Polygon/MultiPolygon. A null geometry means "no clip" — a rectangle
// region's bbox already is its shape, so there is nothing to exclude.
export function pointInPolygon(point, geometry) {
  if (!geometry) return true;
  const polygons =
    geometry.type === "MultiPolygon" ? geometry.coordinates : [geometry.coordinates];
  for (const polygon of polygons) {
    const [outer, ...holes] = polygon;
    if (pointInRing(point, outer) && !holes.some((hole) => pointInRing(point, hole))) return true;
  }
  return false;
}
