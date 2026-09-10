import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css";
import "@geoman-io/leaflet-geoman-free";
import { elevationColor, rankColor } from "../colors";
import { bboxAreaKm2, exceedsTileBudget, ringAreaKm2 } from "../geo";

// Below this zoom the map is for navigation (light labelled street tiles); at or above it the map
// is for judging a site, which needs imagery. Elevation data itself tops out at z15, and regions
// get drawn around z13-15, so imagery is present for the whole drawing/analysis phase.
const SATELLITE_MIN_ZOOM = 12;

// The drawn region reads as a selection, not a result: dashed accent outline with almost no fill,
// so contours and candidate sites inside it stay legible underneath.
const REGION_STYLE = {
  color: "#00b894",
  weight: 2,
  dashArray: "6 4",
  fillColor: "#00b894",
  fillOpacity: 0.06,
};

// Phase 6 moved sizing upstream of ranking, so runoff/capacity/capture arrive WITH the candidate
// (in `properties`) instead of trailing behind the catchment call. Only the catchment *polygon*
// and its warnings still come later via `catchment`, so the popup is useful immediately.
function candidatePopup(properties, catchment) {
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

  let catchmentBlock = `<div class="catchment-pending">Delineating catchment…</div>`;
  if (catchment && catchment.error) {
    catchmentBlock = `<div class="catchment-pending">Catchment outline unavailable: ${catchment.error}</div>`;
  } else if (catchment) {
    catchmentBlock = (catchment.warnings || [])
      .map((w) => `<div class="catchment-warning">⚠ ${w}</div>`)
      .join("");
  }

  // A site that cannot fill in one design storm is the failure Phase 6 exists to surface, so it
  // is called out rather than left for the reader to infer from the numbers.
  if (properties.fill_ratio != null && properties.fill_ratio < 1) {
    catchmentBlock =
      `<div class="catchment-warning">⚠ one design storm fills this to only ` +
      `${(properties.fill_ratio * 100).toFixed(0)}% — it may never fill</div>` +
      catchmentBlock;
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

const MapView = forwardRef(function MapView({ onRegionChange }, ref) {
  const mapEl = useRef(null);
  const mapInstance = useRef(null);
  const layers = useRef({
    contour: null,
    legend: null,
    candidate: null,
    building: null,
    catchment: null,
    pourPoint: null,
  });
  // Plain objects, not React state — these back imperative Leaflet bookkeeping only (which layer
  // is which candidate, what its catchment result was), never anything rendered by this component.
  const candidateLayersByIndex = useRef({});
  const catchmentsByIndex = useRef({});
  const regionLayer = useRef(null);
  const regionShape = useRef(null);

  // The map is initialised once, but `onRegionChange` is a fresh closure every render. Reading it
  // through a ref keeps the Geoman handlers bound in that one-time effect from calling a stale one.
  const onRegionChangeRef = useRef(onRegionChange);
  useEffect(() => {
    onRegionChangeRef.current = onRegionChange;
  });

  function describeRegion() {
    const layer = regionLayer.current;
    if (!layer) return null;
    const bounds = layer.getBounds();
    const bbox = [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()];
    const geometry = layer.toGeoJSON().geometry;
    const ring = geometry && geometry.coordinates ? geometry.coordinates[0] : null;
    return {
      bbox,
      // A rectangle's bbox already IS its shape, so there is nothing for the clip step to do.
      // The shape comes from Geoman's own create event rather than being inferred from the
      // vertex count, since a freehand polygon can legitimately have four corners.
      polygon: regionShape.current === "Rectangle" ? null : geometry,
      areaKm2: ring ? ringAreaKm2(ring) : bboxAreaKm2(bbox),
      overLimit: exceedsTileBudget(bbox),
    };
  }

  function emitRegion() {
    onRegionChangeRef.current?.(describeRegion());
  }

  function removeRegionLayer() {
    if (regionLayer.current) {
      mapInstance.current.removeLayer(regionLayer.current);
      regionLayer.current = null;
      regionShape.current = null;
    }
  }

  function adoptRegionLayer(layer, shape) {
    removeRegionLayer(); // exactly one active region at a time
    regionLayer.current = layer;
    regionShape.current = shape;
    if (layer.setStyle) layer.setStyle(REGION_STYLE);

    // Reshaping or dragging the region after the initial draw has to update state too, otherwise
    // the analysis would silently run against the shape as first drawn.
    for (const event of ["pm:edit", "pm:update", "pm:dragend"]) {
      layer.on(event, emitRegion);
    }
    emitRegion();
  }

  useEffect(() => {
    const map = L.map(mapEl.current, { scrollWheelZoom: false }).setView([22.9734, 78.6569], 5);

    // Satellite tiles are photographic and heavy, and the browser only opens ~6 connections per
    // host — so every tile requested for an intermediate pan/zoom frame delays the ones for the
    // view the user actually lands on. Only fetch for settled views, and keep less off-screen
    // buffer.
    const tileOptions = { updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1 };

    // Measured: a full-India satellite view is 32 tiles taking ~3.3s to finish painting, and it
    // shows nothing useful at that scale — no labels, no place names, nothing pond-sized. Since
    // the village dropdowns were removed, finding your area is now pure map navigation, which
    // unlabelled imagery actively works against. So navigate on light labelled street tiles
    // (smaller, and sharded across three subdomains so they aren't stuck behind one host's
    // connection limit), and switch to imagery once zoomed in far enough for it to mean something.
    const streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 19,
      ...tileOptions,
    });
    const satellite = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles &copy; Esri", maxZoom: 18, ...tileOptions }
    );

    const syncBasemap = () => {
      const wantSatellite = map.getZoom() >= SATELLITE_MIN_ZOOM;
      // Add the incoming layer before removing the outgoing one, so the map never flashes empty.
      if (wantSatellite && !map.hasLayer(satellite)) {
        map.addLayer(satellite);
        map.removeLayer(streets);
      } else if (!wantSatellite && !map.hasLayer(streets)) {
        map.addLayer(streets);
        map.removeLayer(satellite);
      }
    };
    syncBasemap();
    map.on("zoomend", syncBasemap);

    // Rectangle and polygon are the two shapes that describe an area; everything else Geoman
    // offers (markers, lines, circles, text) cannot be a region, and cut/rotate are noise here.
    map.pm.addControls({
      position: "topleft",
      drawMarker: false,
      drawCircle: false,
      drawCircleMarker: false,
      drawPolyline: false,
      drawText: false,
      drawRectangle: true,
      drawPolygon: true,
      cutPolygon: false,
      rotateMode: false,
      editMode: true,
      dragMode: true,
      removalMode: true,
    });

    map.on("pm:create", (e) => {
      if (e.shape !== "Rectangle" && e.shape !== "Polygon") {
        // Not a region shape — drop it rather than leaving an unusable layer on the map.
        map.removeLayer(e.layer);
        return;
      }
      adoptRegionLayer(e.layer, e.shape);
    });

    map.on("pm:remove", (e) => {
      if (e.layer === regionLayer.current) {
        regionLayer.current = null;
        regionShape.current = null;
        emitRegion();
      }
    });

    // Leaflet caches container size and doesn't always pick up a resize on its own; a stale
    // cached size makes fitBounds compute the wrong center.
    const onResize = () => map.invalidateSize();
    window.addEventListener("resize", onResize);

    mapInstance.current = map;
    return () => {
      window.removeEventListener("resize", onResize);
      map.remove();
      mapInstance.current = null;
      regionLayer.current = null;
      regionShape.current = null;
    };
  }, []);

  function clearCandidatesInternal() {
    const map = mapInstance.current;
    for (const key of ["candidate", "building", "catchment", "pourPoint"]) {
      if (layers.current[key]) {
        map.removeLayer(layers.current[key]);
        layers.current[key] = null;
      }
    }
    candidateLayersByIndex.current = {};
    catchmentsByIndex.current = {};
  }

  // Deliberately distinct from the filled candidate zones: a dashed blue outline with almost no
  // fill, so a catchment (which is often far larger than the site) frames it instead of burying it.
  function drawCatchmentGeometry(catchment) {
    const map = mapInstance.current;
    if (layers.current.catchment) {
      map.removeLayer(layers.current.catchment);
      layers.current.catchment = null;
    }
    if (layers.current.pourPoint) {
      map.removeLayer(layers.current.pourPoint);
      layers.current.pourPoint = null;
    }
    if (!catchment || catchment.error || !catchment.geometry) return;

    layers.current.catchment = L.geoJSON(catchment.geometry, {
      style: { color: "#2f9bff", weight: 2, dashArray: "6 4", fillColor: "#2f9bff", fillOpacity: 0.12 },
      interactive: false,
    }).addTo(map);

    const { lat, lon } = catchment.pour_point;
    layers.current.pourPoint = L.circleMarker([lat, lon], {
      radius: 5,
      color: "#ffffff",
      weight: 2,
      fillColor: "#2f9bff",
      fillOpacity: 1,
    })
      .bindTooltip("Pour point (outlet)", { direction: "top" })
      .addTo(map);
  }

  useImperativeHandle(ref, () => ({
    clearRegion() {
      removeRegionLayer();
      emitRegion();
    },

    setContours(body) {
      const map = mapInstance.current;
      if (layers.current.contour) {
        map.removeLayer(layers.current.contour);
        layers.current.contour = null;
      }
      if (layers.current.legend) {
        map.removeControl(layers.current.legend);
        layers.current.legend = null;
      }
      if (!body) return;

      const { min, max } = body.elevation_range;
      layers.current.contour = L.geoJSON(body, {
        style: (feature) => {
          const t = max > min ? (feature.properties.elevation - min) / (max - min) : 0.5;
          const color = elevationColor(t);
          return { fillColor: color, fillOpacity: 0.55, color, weight: 1 };
        },
      }).addTo(map);

      const legend = L.control({ position: "bottomright" });
      legend.onAdd = () => {
        const div = L.DomUtil.create("div", "legend");
        const steps = 8;
        const parts = [];
        for (let i = 0; i <= steps; i++) {
          parts.push(`${elevationColor(i / steps)} ${(i / steps) * 100}%`);
        }
        div.innerHTML = `
          <div class="legend-title">Elevation (m)</div>
          <div class="legend-bar" style="background: linear-gradient(to right, ${parts.join(", ")})"></div>
          <div class="legend-labels"><span>${min.toFixed(0)}</span><span>${max.toFixed(0)}</span></div>
        `;
        return div;
      };
      legend.addTo(map);
      layers.current.legend = legend;
    },

    setCandidates(body) {
      const map = mapInstance.current;
      clearCandidatesInternal();
      if (!body) return;

      layers.current.candidate = L.geoJSON(body, {
        style: (feature) => ({
          color: rankColor(feature.properties.rank),
          weight: 2,
          fillColor: rankColor(feature.properties.rank),
          fillOpacity: 0.5,
        }),
        onEachFeature: (feature, layer) => {
          const index = body.features.indexOf(feature);
          candidateLayersByIndex.current[index] = layer;
          layer.bindPopup(candidatePopup(feature.properties, catchmentsByIndex.current[index]));
          layer.on("popupopen", () => drawCatchmentGeometry(catchmentsByIndex.current[index]));
          layer.bindTooltip(`#${feature.properties.rank}`, {
            permanent: true,
            direction: "center",
            className: "candidate-rank-tooltip",
          });
        },
      }).addTo(map);
    },

    setBuildings(body) {
      const map = mapInstance.current;
      if (layers.current.building) {
        map.removeLayer(layers.current.building);
        layers.current.building = null;
      }
      if (!body || !body.features || body.features.length === 0) return;
      layers.current.building = L.geoJSON(body, {
        style: { color: "#ff3b30", weight: 1, fillColor: "#ff3b30", fillOpacity: 0.25 },
        interactive: false,
      }).addTo(map);
    },

    // Only refreshes popup *content*; it deliberately does not redraw the catchment outline for an
    // already-open popup (matches the original app.js behaviour — click again to see the outline).
    setCatchment(index, data) {
      catchmentsByIndex.current[index] = data;
      const layer = candidateLayersByIndex.current[index];
      if (!layer) return;
      layer.setPopupContent(candidatePopup(layer.feature.properties, data));
    },

    flyToCandidate(index) {
      const layer = candidateLayersByIndex.current[index];
      if (!layer) return;
      mapInstance.current.flyToBounds(layer.getBounds(), { padding: [60, 60], maxZoom: 17 });
      layer.openPopup();
    },
  }));

  return <div id="map" ref={mapEl} />;
});

export default MapView;
