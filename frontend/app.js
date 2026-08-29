const API_BASE = "http://127.0.0.1:8000/api";

// Scroll-to-zoom is disabled — zooming a satellite/terrain map by accidental scroll (e.g. while
// scrolling the page) is disorienting; zoom only via the +/- control.
const map = L.map("map", { scrollWheelZoom: false }).setView([22.9734, 78.6569], 5); // India, default view

L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  {
    attribution: "Tiles &copy; Esri",
    maxZoom: 18,
  }
).addTo(map);

// Leaflet-Geoman draw controls (polygon drawing wired up in Phase 3.8).
map.pm.addControls({
  position: "topleft",
  drawMarker: false,
  drawCircle: false,
  drawCircleMarker: false,
  drawPolyline: false,
  drawRectangle: false,
  drawText: false,
});

window.addEventListener("resize", () => map.invalidateSize());

const stateInput = document.getElementById("state-input");
const stateList = document.getElementById("state-list");
const districtInput = document.getElementById("district-input");
const districtList = document.getElementById("district-list");
const villageInput = document.getElementById("village-input");
const villageList = document.getElementById("village-list");
const villageHint = document.getElementById("village-hint");
const goBtn = document.getElementById("go-btn");
const contoursBtn = document.getElementById("contours-btn");
const candidatesBtn = document.getElementById("candidates-btn");
const statusMsg = document.getElementById("status-msg");

let districtsByName = {};
let villagesById = {};
let regionBoundary = null;
let villagesAvailableForDistrict = false;
let contourLayer = null;
let legendControl = null;
let candidateLayer = null;
let buildingLayer = null;
let catchmentLayer = null;
let pourPointMarker = null;
let catchmentsByIndex = {};

function setStatus(text) {
  statusMsg.textContent = text;
}

// A type-to-filter dropdown backed by a plain text <input> + an absolutely-positioned list of
// matches, since a native <select> only supports keyboard type-ahead (jump-to-first-match), not
// substring filtering as you type. Selection only happens via click or Enter on a highlighted/
// sole match — typing alone never fires onSelect, matching how a native select behaved (you had
// to land on an option, not just start spelling it).
class Combobox {
  constructor({ input, list, onSelect, onClear }) {
    this.input = input;
    this.list = list;
    this.onSelect = onSelect;
    this.onClear = onClear;
    this.items = [];
    this.filtered = [];
    this.activeIndex = -1;
    this.selectedValue = null;

    input.addEventListener("input", () => this.handleInput());
    input.addEventListener("focus", () => this.handleInput());
    input.addEventListener("keydown", (e) => this.handleKeydown(e));
    // mousedown on a list item fires before this blur, so the click still registers.
    input.addEventListener("blur", () => setTimeout(() => this.close(), 150));
  }

  setItems(items) {
    this.items = items;
  }

  clear() {
    this.input.value = "";
    this.selectedValue = null;
    this.close();
  }

  disable() {
    this.input.disabled = true;
    this.clear();
  }

  enable() {
    this.input.disabled = false;
  }

  handleInput() {
    const query = this.input.value.trim().toLowerCase();
    this.filtered = query
      ? this.items.filter((i) => i.label.toLowerCase().includes(query))
      : this.items;
    this.activeIndex = -1;
    this.render();
    if (this.input.value === "" && this.onClear) {
      this.selectedValue = null;
      this.onClear();
    }
  }

  render() {
    this.list.innerHTML = "";
    if (this.filtered.length === 0) {
      this.list.hidden = true;
      return;
    }
    this.filtered.slice(0, 50).forEach((item, i) => {
      const div = document.createElement("div");
      div.className = "combo-item" + (i === this.activeIndex ? " active" : "");
      div.textContent = item.label;
      div.addEventListener("mousedown", (e) => {
        e.preventDefault(); // keep focus so blur-close doesn't win the race against this click
        this.select(item);
      });
      this.list.appendChild(div);
    });
    this.list.hidden = false;
  }

  select(item) {
    this.input.value = item.label;
    this.selectedValue = item.value;
    this.close();
    this.onSelect(item.value, item);
  }

  close() {
    this.list.hidden = true;
    this.activeIndex = -1;
  }

  handleKeydown(e) {
    if (this.list.hidden && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      this.handleInput();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      this.activeIndex = Math.min(this.activeIndex + 1, this.filtered.length - 1);
      this.render();
      this.scrollActiveIntoView();
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      this.activeIndex = Math.max(this.activeIndex - 1, 0);
      this.render();
      this.scrollActiveIntoView();
    } else if (e.key === "Enter") {
      e.preventDefault();
      const pick =
        this.activeIndex >= 0
          ? this.filtered[this.activeIndex]
          : this.filtered.length === 1
            ? this.filtered[0]
            : null;
      if (pick) this.select(pick);
    } else if (e.key === "Escape") {
      this.close();
    }
  }

  scrollActiveIntoView() {
    const el = this.list.children[this.activeIndex];
    if (el) el.scrollIntoView({ block: "nearest" });
  }
}

function resetVillages() {
  villageCombo.disable();
  villageInput.placeholder = "Select district first…";
  villageHint.textContent = "";
  villagesById = {};
  villagesAvailableForDistrict = false;
}

function resetDistrictsAndBelow() {
  districtCombo.disable();
  districtInput.placeholder = "Select state first…";
  resetVillages();
  goBtn.disabled = true;
}

async function loadStates() {
  setStatus("Loading states…");
  try {
    const res = await fetch(`${API_BASE}/states`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const states = await res.json();
    stateCombo.setItems(states.map((s) => ({ label: s.name, value: s.name })));
    stateInput.placeholder = "Type a state…";
    setStatus(`Loaded ${states.length} states`);
  } catch (err) {
    setStatus(`Failed to load states: ${err.message}. Is the backend running?`);
  }
}

async function loadDistricts(stateName) {
  setStatus(`Loading districts for ${stateName}…`);
  resetContours();
  resetDistrictsAndBelow();
  try {
    const res = await fetch(`${API_BASE}/districts?state=${encodeURIComponent(stateName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const districts = await res.json();
    districtsByName = {};
    for (const d of districts) districtsByName[d.name] = d;
    districtCombo.setItems(districts.map((d) => ({ label: d.name, value: d.name })));
    districtCombo.enable();
    districtInput.placeholder = "Type a district…";
    setStatus(`Loaded ${districts.length} districts`);
  } catch (err) {
    setStatus(`Failed to load districts: ${err.message}`);
  }
}

// Guards against out-of-order responses: rapid district reselection (e.g. fast typing that
// matches several districts in a row before settling) can have an earlier district's fetch
// resolve after a later one and clobber the UI with stale data. Only the response matching the
// most recently issued request is applied.
let villageRequestId = 0;

async function loadVillages(districtName) {
  resetVillages();
  // Village is required once data exists for this district (see goBtn logic below) — until
  // that's known, keep Enter disabled rather than letting the user skip past it.
  goBtn.disabled = true;
  const requestId = ++villageRequestId;
  try {
    const res = await fetch(`${API_BASE}/villages?district=${encodeURIComponent(districtName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const villages = await res.json();
    if (requestId !== villageRequestId) return; // a newer district selection superseded this

    if (villages.length === 0) {
      villageHint.textContent = "(no village data for this district yet — district-level OK)";
      goBtn.disabled = false; // can't require what doesn't exist
      return;
    }
    for (const v of villages) villagesById[v.id] = v;
    villageCombo.setItems(
      villages.map((v) => ({
        label: v.gp_name ? `${v.name} (${v.gp_name})` : v.name,
        value: v.id,
      }))
    );
    villageCombo.enable();
    villageInput.placeholder = "Type a village…";
    villageHint.textContent = `(${villages.length} available — required)`;
    villagesAvailableForDistrict = true;
    // goBtn stays disabled until villageCombo's onSelect sees a real pick
  } catch (err) {
    if (requestId !== villageRequestId) return;
    villageHint.textContent = "(failed to load villages)";
    goBtn.disabled = false; // don't block the whole flow on a village-fetch failure
  }
}

function goToRegion(label, region) {
  const { centroid, bbox } = region;
  const [minLon, minLat, maxLon, maxLat] = bbox;

  if (regionBoundary) {
    map.removeLayer(regionBoundary);
  }

  // Leaflet caches container size and doesn't always pick up a resize on its own;
  // a stale cached size makes fitBounds compute the wrong center. Force a resync first.
  map.invalidateSize();

  map.fitBounds([
    [minLat, minLon],
    [maxLat, maxLon],
  ]);

  contoursBtn.disabled = false;
  candidatesBtn.disabled = false;
  setStatus(`${label}: centered at ${centroid.lat.toFixed(4)}, ${centroid.lon.toFixed(4)}`);
}

// Interpolates a diverging blue -> tan -> red ramp, low elevation to high.
function elevationColor(t) {
  const stops = [
    [0.0, [33, 102, 172]],
    [0.25, [103, 169, 207]],
    [0.5, [253, 219, 199]],
    [0.75, [239, 138, 98]],
    [1.0, [178, 24, 43]],
  ];
  t = Math.max(0, Math.min(1, t));
  for (let i = 0; i < stops.length - 1; i++) {
    const [t0, c0] = stops[i];
    const [t1, c1] = stops[i + 1];
    if (t >= t0 && t <= t1) {
      const f = t1 === t0 ? 0 : (t - t0) / (t1 - t0);
      const c = c0.map((v, idx) => Math.round(v + (c1[idx] - v) * f));
      return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    }
  }
  return "rgb(178, 24, 43)";
}

function updateLegend(minE, maxE) {
  if (legendControl) {
    map.removeControl(legendControl);
  }
  legendControl = L.control({ position: "bottomright" });
  legendControl.onAdd = () => {
    const div = L.DomUtil.create("div", "legend");
    const steps = 8;
    const parts = [];
    for (let i = 0; i <= steps; i++) {
      parts.push(`${elevationColor(i / steps)} ${(i / steps) * 100}%`);
    }
    div.innerHTML = `
      <div class="legend-title">Elevation (m)</div>
      <div class="legend-bar" style="background: linear-gradient(to right, ${parts.join(", ")})"></div>
      <div class="legend-labels"><span>${minE.toFixed(0)}</span><span>${maxE.toFixed(0)}</span></div>
    `;
    return div;
  };
  legendControl.addTo(map);
}

async function loadContours() {
  const bounds = map.getBounds();
  const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(",");
  setStatus("Loading contours…");
  contoursBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/contours?bbox=${encodeURIComponent(bbox)}&interval=2`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);

    if (contourLayer) {
      map.removeLayer(contourLayer);
    }
    const { min, max } = body.elevation_range;
    contourLayer = L.geoJSON(body, {
      style: (feature) => {
        const t = max > min ? (feature.properties.elevation - min) / (max - min) : 0.5;
        const color = elevationColor(t);
        return { fillColor: color, fillOpacity: 0.55, color, weight: 1 };
      },
    }).addTo(map);
    updateLegend(min, max);
    setStatus(`Loaded ${body.features.length} contour bands (${min.toFixed(0)}–${max.toFixed(0)}m)`);
  } catch (err) {
    setStatus(`Failed to load contours: ${err.message}`);
  } finally {
    contoursBtn.disabled = false;
  }
}

contoursBtn.addEventListener("click", loadContours);

// Rank 1 is the strongest green and fades down the list, so the ordering is readable at a
// glance without having to open each popup.
const RANK_COLORS = ["#00e676", "#66dd55", "#a8d63a", "#d4c22c", "#f0a020"];

function rankColor(rank) {
  return RANK_COLORS[Math.min(rank - 1, RANK_COLORS.length - 1)];
}

// Phase 6 moved sizing upstream of ranking, so runoff/capacity/capture now arrive WITH the
// candidates instead of trailing behind the catchment call. Only the catchment *polygon* and its
// warnings still come later, so the popup is useful immediately.
function candidatePopup(properties, featureIndex) {
  const rows = [
    ["Area", `${properties.area_ha.toFixed(2)} ha`],
    ["Mean depth", `${properties.mean_depth_m.toFixed(2)} m`],
    ["Max depth", `${properties.max_depth_m.toFixed(2)} m`],
    ["Compactness", properties.compactness.toFixed(3)],
  ];

  if (properties.catchment_area_m2 != null) {
    const catchHa = properties.catchment_area_m2 / 10000;
    const ratio = catchHa / properties.area_ha;
    rows.push(["Catchment", `${catchHa.toFixed(1)} ha`]);
    rows.push(["Catchment ratio", `${ratio.toFixed(0)}×`]);
  }
  if (properties.capacity_m3 != null) {
    rows.push(["Pond capacity", `${Math.round(properties.capacity_m3).toLocaleString()} m³`]);
  }
  if (properties.runoff_m3 != null) {
    rows.push(["Storm runoff", `${Math.round(properties.runoff_m3).toLocaleString()} m³`]);
  }
  if (properties.capture_fraction != null) {
    rows.push(["Captures", `${(properties.capture_fraction * 100).toFixed(0)}% of one storm`]);
  }
  if (properties.fill_ratio != null) {
    const fill = properties.fill_ratio;
    rows.push(["Fills", fill >= 1 ? `${fill.toFixed(1)}× over` : `${(fill * 100).toFixed(0)}% full`]);
  }

  const catchment = catchmentsByIndex[featureIndex];
  let catchmentBlock = `<div class="catchment-pending">Delineating catchment…</div>`;
  if (catchment && catchment.error) {
    catchmentBlock = `<div class="catchment-pending">Catchment outline unavailable: ${catchment.error}</div>`;
  } else if (catchment) {
    catchmentBlock = (catchment.warnings || [])
      .map((w) => `<div class="catchment-warning">⚠ ${w}</div>`)
      .join("");
  }

  // A site that cannot fill in one design storm is the failure the Phase 6 rework exists to
  // surface, so it is called out rather than left for the reader to infer from the numbers.
  if (properties.fill_ratio != null && properties.fill_ratio < 1) {
    catchmentBlock =
      `<div class="catchment-warning">⚠ one design storm fills this to only ` +
      `${(properties.fill_ratio * 100).toFixed(0)}% — it may never fill</div>` + catchmentBlock;
  }

  return `
    <div class="candidate-popup">
      <h4><span class="rank-badge">#${properties.rank}</span> Candidate site</h4>
      <table>${rows
        .map(([label, value]) => `<tr><td>${label}</td><td>${value}</td></tr>`)
        .join("")}</table>
      ${catchmentBlock}
      <div class="candidate-caveat">
        Screened against known water bodies (2021 satellite data) — <strong>not</strong> a check of
        land ownership or availability. A shortlist to visit, not an approval.
      </div>
    </div>`;
}

function showCatchment(featureIndex) {
  const catchment = catchmentsByIndex[featureIndex];
  if (catchmentLayer) {
    map.removeLayer(catchmentLayer);
    catchmentLayer = null;
  }
  if (pourPointMarker) {
    map.removeLayer(pourPointMarker);
    pourPointMarker = null;
  }
  if (!catchment || catchment.error || !catchment.geometry) return;

  // Deliberately distinct from the filled candidate zones: a dashed blue outline with almost no
  // fill, so a catchment (which is often far larger) frames the site instead of burying it.
  catchmentLayer = L.geoJSON(catchment.geometry, {
    style: { color: "#2f9bff", weight: 2, dashArray: "6 4", fillColor: "#2f9bff", fillOpacity: 0.12 },
    interactive: false,
  }).addTo(map);

  const { lat, lon } = catchment.pour_point;
  pourPointMarker = L.circleMarker([lat, lon], {
    radius: 5,
    color: "#ffffff",
    weight: 2,
    fillColor: "#2f9bff",
    fillOpacity: 1,
  })
    .bindTooltip("Pour point (outlet)", { direction: "top" })
    .addTo(map);
}

// Catchment + rainfall run after candidates are already drawn: the flow solve takes ~10s, and
// blocking the candidate results behind it would repeat the buildings-layer mistake.
async function loadCatchments(bbox, features) {
  try {
    const res = await fetch(`${API_BASE}/catchment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ bbox, polygons: features.map((f) => f.geometry) }),
    });
    if (!res.ok) return;
    const body = await res.json();

    // No per-candidate rainfall fetch any more: rainfall is constant across a viewport (a ~10 km
    // view sits inside one ~25 km ERA5 cell), so /api/candidates fetches it once and the sizing
    // numbers already arrived with the candidates. This call now only supplies catchment outlines.
    for (const result of body.results) {
      catchmentsByIndex[result.index] = result;
    }

    // Popups were bound before these numbers existed — rebind so an already-open one updates.
    if (candidateLayer) {
      candidateLayer.getLayers().forEach((layer, index) => {
        layer.setPopupContent(candidatePopup(layer.feature.properties, index));
      });
    }
    setStatus(`${statusMsg.textContent} · catchments ready — click a site`);
  } catch (err) {
    /* soft-fail: candidates remain usable without catchment figures */
  }
}

function clearCandidates() {
  for (const layer of [candidateLayer, buildingLayer, catchmentLayer, pourPointMarker]) {
    if (layer) map.removeLayer(layer);
  }
  candidateLayer = null;
  buildingLayer = null;
  catchmentLayer = null;
  pourPointMarker = null;
  catchmentsByIndex = {};
}

async function loadBuildings(bbox) {
  // Warning layer only — a failure here must never block the candidate results.
  try {
    const res = await fetch(`${API_BASE}/buildings?bbox=${encodeURIComponent(bbox)}`);
    if (!res.ok) return;
    const body = await res.json();
    if (!body.features || body.features.length === 0) return;
    buildingLayer = L.geoJSON(body, {
      style: { color: "#ff3b30", weight: 1, fillColor: "#ff3b30", fillOpacity: 0.25 },
      interactive: false,
    }).addTo(map);
  } catch (err) {
    /* soft-fail: map simply has no building overlay */
  }
}

async function loadCandidates() {
  const bounds = map.getBounds();
  const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(",");
  setStatus("Finding pond sites…");
  candidatesBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/candidates?bbox=${encodeURIComponent(bbox)}&top_n=5`);
    const body = await res.json();
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);

    clearCandidates();
    const summary = body.summary;

    if (body.features.length === 0) {
      setStatus(
        `No suitable sites in view — ${summary.zones_in_view} depressions checked, all filtered ` +
          `out (${summary.excluded_water} on/near existing water, ${summary.excluded_shape} drainage-like).`
      );
      return;
    }

    candidateLayer = L.geoJSON(body, {
      style: (feature) => ({
        color: rankColor(feature.properties.rank),
        weight: 2,
        fillColor: rankColor(feature.properties.rank),
        fillOpacity: 0.5,
      }),
      onEachFeature: (feature, layer) => {
        const featureIndex = body.features.indexOf(feature);
        layer.bindPopup(candidatePopup(feature.properties, featureIndex));
        layer.on("popupopen", () => showCatchment(featureIndex));
        layer.bindTooltip(`#${feature.properties.rank}`, {
          permanent: true,
          direction: "center",
          className: "candidate-rank-tooltip",
        });
      },
    }).addTo(map);

    // Deliberately not awaited: buildings are an optional warning layer, and when Overpass is
    // slow or unreachable awaiting it left the results invisible behind a "Finding pond sites…"
    // status for ~20s even though the candidates were already drawn. Let it fill in late.
    loadBuildings(bbox);

    // Be explicit when a source was unavailable rather than implying a full screen ran (9.6).
    const degraded = [];
    if (!summary.worldcover_available) degraded.push("WorldCover");
    if (!summary.swir_available) degraded.push("SWIR");
    if (!summary.overpass_available) degraded.push("OSM");
    if (!summary.rainfall_available) degraded.push("rainfall");
    const caveat = degraded.length ? ` — unavailable: ${degraded.join(", ")}` : "";

    setStatus(
      `Top ${summary.returned} of ${summary.eligible} sites ` +
        `(${summary.excluded_water} on/near existing water, ${summary.excluded_shape} drainage-like, excluded) ` +
        `· ${summary.design_storm_mm.toFixed(0)} mm design storm${caveat}`
    );

    loadCatchments(bbox, body.features); // background, same reasoning as buildings above
  } catch (err) {
    setStatus(`Failed to find pond sites: ${err.message}`);
  } finally {
    candidatesBtn.disabled = false;
  }
}

candidatesBtn.addEventListener("click", loadCandidates);

function resetContours() {
  if (contourLayer) {
    map.removeLayer(contourLayer);
    contourLayer = null;
  }
  if (legendControl) {
    map.removeControl(legendControl);
    legendControl = null;
  }
  clearCandidates();
  contoursBtn.disabled = true;
  candidatesBtn.disabled = true;
}

const stateCombo = new Combobox({
  input: stateInput,
  list: stateList,
  onSelect: (stateName) => loadDistricts(stateName),
  onClear: () => {
    resetContours();
    resetDistrictsAndBelow();
  },
});

const districtCombo = new Combobox({
  input: districtInput,
  list: districtList,
  onSelect: (districtName) => {
    resetContours();
    loadVillages(districtName); // sets goBtn state itself once it knows if villages exist
  },
  onClear: () => {
    resetContours();
    resetVillages();
    goBtn.disabled = true;
  },
});

const villageCombo = new Combobox({
  input: villageInput,
  list: villageList,
  onSelect: () => {
    if (villagesAvailableForDistrict) goBtn.disabled = false;
  },
  onClear: () => {
    if (villagesAvailableForDistrict) goBtn.disabled = true;
  },
});

districtCombo.disable();
villageCombo.disable();

goBtn.addEventListener("click", () => {
  // Village (if picked) gives a tighter, more useful starting zoom than the whole district.
  const village = villagesById[villageCombo.selectedValue];
  if (village) {
    goToRegion(village.name, village);
  } else if (districtCombo.selectedValue) {
    goToRegion(districtCombo.selectedValue, districtsByName[districtCombo.selectedValue]);
  }
});

loadStates();
