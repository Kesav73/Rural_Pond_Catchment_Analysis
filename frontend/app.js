// Phase 0 scaffold: prove Leaflet + Leaflet-Geoman load and render a working map.
// State/district dropdown wiring against the backend happens in Phase 1.

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

document.getElementById("status-msg").textContent = "Map loaded (Phase 0 scaffold)";
