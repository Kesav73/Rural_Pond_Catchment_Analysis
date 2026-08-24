const API_BASE = "http://127.0.0.1:8000/api";

const map = L.map("map").setView([22.9734, 78.6569], 5); // India, default view

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

const stateSelect = document.getElementById("state-select");
const districtSelect = document.getElementById("district-select");
const villageSelect = document.getElementById("village-select");
const villageHint = document.getElementById("village-hint");
const goBtn = document.getElementById("go-btn");
const statusMsg = document.getElementById("status-msg");

let districtsByName = {};
let villagesById = {};
let regionBoundary = null;
let villagesAvailableForDistrict = false;

function setStatus(text) {
  statusMsg.textContent = text;
}

function resetSelect(select, placeholder) {
  select.innerHTML = "";
  const opt = document.createElement("option");
  opt.value = "";
  opt.textContent = placeholder;
  select.appendChild(opt);
}

async function loadStates() {
  setStatus("Loading states…");
  try {
    const res = await fetch(`${API_BASE}/states`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const states = await res.json();
    resetSelect(stateSelect, "Select state…");
    for (const s of states) {
      const opt = document.createElement("option");
      opt.value = s.name;
      opt.textContent = s.name;
      stateSelect.appendChild(opt);
    }
    stateSelect.disabled = false;
    setStatus(`Loaded ${states.length} states`);
  } catch (err) {
    setStatus(`Failed to load states: ${err.message}. Is the backend running?`);
  }
}

async function loadDistricts(stateName) {
  setStatus(`Loading districts for ${stateName}…`);
  resetSelect(districtSelect, "Select district…");
  districtSelect.disabled = true;
  resetVillages();
  goBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/districts?state=${encodeURIComponent(stateName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const districts = await res.json();
    districtsByName = {};
    for (const d of districts) {
      districtsByName[d.name] = d;
      const opt = document.createElement("option");
      opt.value = d.name;
      opt.textContent = d.name;
      districtSelect.appendChild(opt);
    }
    districtSelect.disabled = false;
    setStatus(`Loaded ${districts.length} districts`);
  } catch (err) {
    setStatus(`Failed to load districts: ${err.message}`);
  }
}

function resetVillages() {
  resetSelect(villageSelect, "Select village…");
  villageSelect.disabled = true;
  villageHint.textContent = "";
  villagesById = {};
  villagesAvailableForDistrict = false;
}

async function loadVillages(districtName) {
  resetVillages();
  // Village is required once data exists for this district (see goBtn logic below) — until
  // that's known, keep Enter disabled rather than letting the user skip past it.
  goBtn.disabled = true;
  try {
    const res = await fetch(`${API_BASE}/villages?district=${encodeURIComponent(districtName)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const villages = await res.json();
    if (villages.length === 0) {
      villageHint.textContent = "(no village data for this district yet — district-level OK)";
      goBtn.disabled = false; // can't require what doesn't exist
      return;
    }
    for (const v of villages) {
      villagesById[v.id] = v;
      const opt = document.createElement("option");
      opt.value = v.id;
      opt.textContent = v.gp_name ? `${v.name} (${v.gp_name})` : v.name;
      villageSelect.appendChild(opt);
    }
    villageSelect.disabled = false;
    villageHint.textContent = `(${villages.length} available — required)`;
    villagesAvailableForDistrict = true;
    // goBtn stays disabled until villageSelect's own change listener sees a real pick
  } catch (err) {
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

  setStatus(`${label}: centered at ${centroid.lat.toFixed(4)}, ${centroid.lon.toFixed(4)}`);
}

stateSelect.addEventListener("change", () => {
  const stateName = stateSelect.value;
  if (!stateName) {
    resetSelect(districtSelect, "Select district…");
    districtSelect.disabled = true;
    resetVillages();
    goBtn.disabled = true;
    return;
  }
  loadDistricts(stateName);
});

districtSelect.addEventListener("change", () => {
  if (districtSelect.value) {
    loadVillages(districtSelect.value); // sets goBtn state itself once it knows if villages exist
  } else {
    resetVillages();
    goBtn.disabled = true;
  }
});

villageSelect.addEventListener("change", () => {
  if (villagesAvailableForDistrict) {
    goBtn.disabled = !villageSelect.value;
  }
});

goBtn.addEventListener("click", () => {
  // Village (if picked) gives a tighter, more useful starting zoom than the whole district.
  const village = villagesById[villageSelect.value];
  if (village) {
    goToRegion(village.name, village);
  } else if (districtSelect.value) {
    goToRegion(districtSelect.value, districtsByName[districtSelect.value]);
  }
});

loadStates();
