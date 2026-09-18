import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "@geoman-io/leaflet-geoman-free/dist/leaflet-geoman.css";
import "@geoman-io/leaflet-geoman-free";
import { elevationColor, rankColor } from "../colors";
import { bboxAreaKm2, exceedsTileBudget, ringAreaKm2 } from "../geo";
import * as fmt from "../format";

// Below this zoom the map is for navigation (light labelled street tiles); at or above it the map
// is for judging a site, which needs imagery. Elevation data itself tops out at z15, and regions
// get drawn around z13-15, so imagery is present for the whole drawing/analysis phase.
const SATELLITE_MIN_ZOOM = 12;

// Opening view: the whole of India, whatever the window size (a fixed centre/zoom left India cut
// off on the right once the sidebar took its 360px).
const INDIA_BOUNDS = [
  [6.5, 68.0],
  [35.7, 97.4],
];

// Wheel / trackpad feel. Leaflet's defaults step a whole zoom level per notch, which on a trackpad
// is jumpy; quarter-level snapping with a longer wheel distance per level gives smooth zooming
// without making a mouse wheel sluggish. Pinch on a trackpad arrives as ctrl+wheel and uses the
// same path.
const ZOOM_OPTIONS = {
  scrollWheelZoom: true,
  zoomSnap: 0.25,
  zoomDelta: 0.5,
  wheelPxPerZoomLevel: 90,
  wheelDebounceTime: 30,
};

// Stacking, bottom to top: contours < rejected hollows < catchments < ponds < place names < markers
// (Leaflet's own markerPane, 600) < tooltips < popups. Catchments are usually far larger than the
// pond they feed, so they sit underneath it and frame it rather than burying it.
const PANES = { contours: 350, excluded: 380, catchments: 400, ponds: 420, labels: 450 };

// The drawn region reads as a selection, not a result: dashed accent outline with almost no fill,
// so contours and candidate sites inside it stay legible underneath.
const REGION_STYLE = {
  color: "#00b894",
  weight: 2,
  dashArray: "6 4",
  fillColor: "#00b894",
  fillOpacity: 0.04,
};

// White reads on imagery and stays clear of the green→orange rank colours used by sites and
// catchments.
const CONTOUR_LINE_COLOR = "#f4f7ff";

// The map answers one question at a glance — where are the ponds, and in what order — so ponds are
// drawn as water (blue), the one thing on the map that looks like a pond. A pond's catchment is
// land, not a pond, and is drawn only for the pond the user has selected, in a distinct amber, so
// the two can never be mistaken for each other.
const POND_COLOR = "#2f8cff";
const CATCHMENT_COLOR = "#ffc542";

const CATCHMENT_STYLE = {
  color: CATCHMENT_COLOR,
  weight: 2.5,
  dashArray: "8 6",
  fillColor: CATCHMENT_COLOR,
  fillOpacity: 0.14,
};

function pondStyle(selected) {
  return {
    color: "#ffffff",
    weight: selected ? 3 : 1.5,
    fillColor: POND_COLOR,
    fillOpacity: selected ? 0.85 : 0.65,
  };
}

// Rank only — the details live behind a click.
function rankMarkerIcon(properties, selected) {
  return L.divIcon({
    className: "rank-pin-anchor",
    html:
      `<div class="rank-pin${selected ? " is-selected" : ""}" style="--rank-color:${rankColor(properties.rank)}">` +
      `<span class="rank-pin-num">${properties.rank}</span></div>`,
    iconSize: null, // sized by CSS
  });
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]
  );
}

function rowsHtml(rows) {
  return `<table>${rows
    .map(([label, value]) => `<tr><td>${label}</td><td>${value}</td></tr>`)
    .join("")}</table>`;
}

// Sizing arrives with the candidate itself; only the catchment *outline* and its warnings come
// later via `catchment`, so the popup is complete apart from that one section immediately.
function candidatePopup(properties, catchment, designStormMm) {
  const volume = fmt.expectedVolume(properties);
  const status = fmt.fillStatus(properties);

  const water = [];
  if (properties.capacity_m3 != null) water.push(["Pond capacity", fmt.m3(properties.capacity_m3)]);
  if (properties.runoff_m3 != null) {
    const storm = designStormMm ? ` (${Math.round(designStormMm)} mm)` : "";
    water.push([`Runoff, one storm${storm}`, fmt.m3(properties.runoff_m3)]);
  }
  if (properties.capture_fraction != null) {
    water.push(["Share of runoff kept", fmt.pct(properties.capture_fraction)]);
  }

  const site = [
    ["Pond area", fmt.ha(properties.area_ha, 2)],
    ["Depth (mean / max)", `${properties.mean_depth_m.toFixed(1)} / ${properties.max_depth_m.toFixed(1)} m`],
    ["Shape (compactness)", properties.compactness.toFixed(2)],
  ];

  const catchmentRows = [];
  const catchHa = fmt.catchmentHa(properties, catchment);
  if (catchHa != null) {
    catchmentRows.push(["Catchment area", `${fmt.ha(catchHa)} (${fmt.km2(catchHa)})`]);
    // Same ratio the drainage warning quotes, once the outline exists.
    const ratio = catchment?.catchment_to_pond_ratio ?? catchHa / properties.area_ha;
    catchmentRows.push(["Catchment : pond", `${Math.round(ratio)}×`]);
  }

  const notes = [];
  if (properties.fill_ratio != null && properties.fill_ratio < 1) {
    notes.push(
      `One design storm fills this to only ${fmt.pct(properties.fill_ratio)} — it may never fill.`
    );
  }
  let pending = "";
  if (!catchment) {
    pending = `<div class="popup-pending">Delineating catchment outline…</div>`;
  } else if (catchment.error) {
    pending = `<div class="popup-pending">Catchment outline unavailable: ${escapeHtml(catchment.error)}</div>`;
  } else {
    for (const warning of catchment.warnings || []) notes.push(warning);
  }

  return `
    <div class="candidate-popup">
      <header>
        <span class="rank-badge" style="--rank-color:${rankColor(properties.rank)}">${properties.rank}</span>
        <h4>Pond #${properties.rank}</h4>
        ${status ? `<span class="fill-badge fill-${status.tone}">${status.label}</span>` : ""}
      </header>
      <div class="popup-key">
        <span><i class="key-pond"></i>Pond location</span>
        <span><i class="key-catchment"></i>Its catchment — land whose rain drains into it</span>
      </div>
      ${
        volume != null
          ? `<div class="popup-headline">
               <span class="popup-headline-label">Water collected per heavy storm</span>
               <strong>${fmt.m3(volume)}</strong>
               ${volume >= 100_000 ? `<span>${fmt.lakh(volume)}</span>` : ""}
             </div>`
          : ""
      }
      <section><h5>Water</h5>${rowsHtml(water)}</section>
      <section><h5>Catchment</h5>${rowsHtml(catchmentRows)}${pending}</section>
      <section><h5>Site</h5>${rowsHtml(site)}</section>
      ${notes.map((n) => `<div class="popup-warning">⚠ ${escapeHtml(n)}</div>`).join("")}
      <div class="popup-caveat">
        Screened against known water bodies (2021 satellite data) — <strong>not</strong> a check of
        land ownership or availability. A shortlist to visit, not an approval.
      </div>
    </div>`;
}

function legendHtml(state) {
  const items = [];
  if (state.hasSites) {
    items.push(`<div class="legend-row"><span class="swatch swatch-pond"></span>Pond location</div>`);
    items.push(`<div class="legend-row"><span class="swatch swatch-pin">1</span>Rank (1 = best) — click for details</div>`);
  }
  if (state.hasCatchments) {
    items.push(`<div class="legend-row"><span class="swatch swatch-catchment"></span>Catchment of the selected pond</div>`);
    items.push(`<div class="legend-row"><span class="swatch swatch-pour"></span>Where the water enters the pond</div>`);
  }
  if (state.hasExcluded) {
    items.push(`<div class="legend-row"><span class="swatch swatch-excluded"></span>Rejected hollow (hover for why)</div>`);
  }
  if (state.hasBuildings) {
    items.push(`<div class="legend-row"><span class="swatch swatch-building"></span>Building (OSM)</div>`);
  }
  if (state.contours?.style === "lines") {
    items.push(
      `<div class="legend-row"><span class="swatch swatch-contour"></span>Contour every ${state.contours.interval} m` +
        ` (bold every ${state.contours.majorInterval} m)</div>`
    );
  }
  let ramp = "";
  if (state.contours?.style === "bands") {
    const { min, max } = state.contours.range;
    const stops = [];
    for (let i = 0; i <= 8; i++) stops.push(`${elevationColor(i / 8)} ${(i / 8) * 100}%`);
    ramp = `
      <div class="legend-title">Elevation (m)</div>
      <div class="legend-bar" style="background: linear-gradient(to right, ${stops.join(", ")})"></div>
      <div class="legend-labels"><span>${min.toFixed(0)}</span><span>${max.toFixed(0)}</span></div>`;
  }
  if (!items.length && !ramp) return "";
  return `${items.length ? `<div class="legend-title">Map key</div>${items.join("")}` : ""}${ramp}`;
}

const MapView = forwardRef(function MapView({ onRegionChange, onSelectSite }, ref) {
  const mapEl = useRef(null);
  const mapInstance = useRef(null);
  // Persistent layer groups (created once, emptied and refilled per run) — so the layer control's
  // checkboxes keep working across runs and a user's "hide catchments" choice survives a new run.
  const groups = useRef(null);
  const legendControl = useRef(null);
  const legendState = useRef({});
  // Plain objects, not React state — imperative Leaflet bookkeeping only.
  const sites = useRef({}); // rank -> { feature, pond, marker, catchment, pour, catchmentData }
  const selectedRank = useRef(null);
  const designStormMm = useRef(null);
  const regionLayer = useRef(null);
  const regionShape = useRef(null);
  // Set while the map is being moved programmatically, so only moves the *user* makes count as
  // "the user has taken over the view" (see fitToResults).
  const programmaticMove = useRef(false);
  const userMovedSinceFit = useRef(false);

  // The map is initialised once, but callbacks are fresh closures every render. Reading them
  // through refs keeps handlers bound in the one-time effect from calling stale ones.
  const onRegionChangeRef = useRef(onRegionChange);
  const onSelectSiteRef = useRef(onSelectSite);
  useEffect(() => {
    onRegionChangeRef.current = onRegionChange;
    onSelectSiteRef.current = onSelectSite;
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

  function updateLegend(patch) {
    legendState.current = { ...legendState.current, ...patch };
    const html = legendHtml(legendState.current);
    const container = legendControl.current.getContainer();
    container.innerHTML = html;
    container.style.display = html ? "" : "none";
  }

  function moveMap(fn) {
    programmaticMove.current = true;
    fn();
    // moveend fires once the (possibly animated) move settles.
    mapInstance.current.once("moveend", () => {
      programmaticMove.current = false;
    });
  }

  useEffect(() => {
    // `pmIgnore: false` opts the map itself into Geoman. Opt-in mode (set below) otherwise applies
    // to maps too, so any map built after the first — a remount, a dev hot reload — would come up
    // with no drawing tools at all.
    const map = L.map(mapEl.current, { ...ZOOM_OPTIONS, pmIgnore: false }).fitBounds(INDIA_BOUNDS);

    for (const [name, zIndex] of Object.entries(PANES)) {
      map.createPane(name);
      map.getPane(name).style.zIndex = zIndex;
    }
    // Place names sit above the result overlays (so a village name stays readable over a pond) but
    // below markers, tooltips and popups, and never intercept clicks.
    map.getPane("labels").style.pointerEvents = "none";

    // Satellite tiles are photographic and heavy, and the browser only opens ~6 connections per
    // host — so every tile requested for an intermediate pan/zoom frame delays the ones for the
    // view the user actually lands on. Only fetch for settled views, and keep less off-screen
    // buffer.
    const tileOptions = { updateWhenIdle: true, updateWhenZooming: false, keepBuffer: 1 };

    // Measured: a full-India satellite view is 32 tiles taking ~3.3s to finish painting, and it
    // shows nothing useful at that scale — no labels, no place names, nothing pond-sized. So
    // navigate on light labelled street tiles (smaller, and sharded across three subdomains so
    // they aren't stuck behind one host's connection limit), and switch to imagery once zoomed in
    // far enough for it to mean something.
    const streets = L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap contributors",
      maxZoom: 19,
      ...tileOptions,
    });
    const imagery = () =>
      L.tileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        { attribution: "Imagery &copy; Esri", maxZoom: 18, ...tileOptions }
      );
    // Imagery alone has no names on it, so once the satellite view kicks in you could no longer
    // tell which village you were looking at. Esri's reference layers put place names and roads
    // back on top of it.
    const reference = (service) =>
      L.tileLayer(
        `https://server.arcgisonline.com/ArcGIS/rest/services/Reference/${service}/MapServer/tile/{z}/{y}/{x}`,
        { pane: "labels", maxZoom: 18, ...tileOptions }
      );
    const satelliteLabelled = L.layerGroup([
      imagery(),
      reference("World_Transportation"),
      reference("World_Boundaries_and_Places"),
    ]);
    const satellite = imagery();

    const baseLayers = {
      Streets: streets,
      "Satellite + labels": satelliteLabelled,
      Satellite: satellite,
    };

    groups.current = {
      contours: L.layerGroup().addTo(map),
      excluded: L.layerGroup().addTo(map),
      catchments: L.layerGroup().addTo(map),
      ponds: L.layerGroup().addTo(map),
      buildings: L.layerGroup().addTo(map),
    };
    L.control
      .layers(
        baseLayers,
        {
          Contours: groups.current.contours,
          "Catchment (selected pond)": groups.current.catchments,
          "Pond sites": groups.current.ponds,
          "Rejected hollows": groups.current.excluded,
          Buildings: groups.current.buildings,
        },
        { position: "topright" }
      )
      .addTo(map);

    legendControl.current = L.control({ position: "bottomright" });
    legendControl.current.onAdd = () => {
      const div = L.DomUtil.create("div", "legend");
      div.style.display = "none";
      L.DomEvent.disableScrollPropagation(div);
      return div;
    };
    legendControl.current.addTo(map);

    // Streets for navigating, labelled satellite once zoomed in far enough to judge a site — until
    // the user picks a basemap themselves, after which their choice sticks.
    let userPickedBase = false;
    let autoSwitching = false;
    map.on("baselayerchange", () => {
      if (!autoSwitching) userPickedBase = true;
    });
    const syncBasemap = () => {
      if (userPickedBase) return;
      const want = map.getZoom() >= SATELLITE_MIN_ZOOM ? satelliteLabelled : streets;
      if (map.hasLayer(want)) return;
      autoSwitching = true;
      // Add the incoming layer before removing the outgoing one, so the map never flashes empty.
      map.addLayer(want);
      for (const layer of Object.values(baseLayers)) {
        if (layer !== want && map.hasLayer(layer)) map.removeLayer(layer);
      }
      autoSwitching = false;
    };
    syncBasemap();
    map.on("zoomend", syncBasemap);

    const noteUserMove = () => {
      if (!programmaticMove.current) userMovedSinceFit.current = true;
    };
    map.on("dragstart", noteUserMove);
    map.on("zoomstart", noteUserMove);

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
    // Result layers are outputs, not drawings: Geoman's edit/drag/remove tools must leave them be.
    L.PM.setOptIn(true);

    map.on("pm:create", (e) => {
      if (e.shape !== "Rectangle" && e.shape !== "Polygon") {
        // Not a region shape — drop it rather than leaving an unusable layer on the map.
        map.removeLayer(e.layer);
        return;
      }
      // Opt-in mode above means only layers flagged like this are editable — i.e. the region.
      e.layer.options.pmIgnore = false;
      L.PM.reInitLayer(e.layer);
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
    // Dev-only handle for scripted browser checks (placing a test region precisely); stripped from
    // production builds because `import.meta.env.DEV` is statically false there.
    if (import.meta.env.DEV) window.__pond = { map, L };
    return () => {
      window.removeEventListener("resize", onResize);
      map.remove();
      mapInstance.current = null;
      regionLayer.current = null;
      regionShape.current = null;
    };
  }, []);

  function refreshSite(rank) {
    const site = sites.current[rank];
    if (!site) return;
    const selected = selectedRank.current === rank;
    site.pond.setStyle(pondStyle(selected));
    site.marker.setIcon(rankMarkerIcon(site.feature.properties, selected));
    site.marker.setZIndexOffset(selected ? 1000 : -rank);
    // Only the selected pond's catchment is on the map; drawing all of them made the large amber
    // areas read as "the sites" and buried the ponds themselves.
    const group = groups.current.catchments;
    for (const layer of [site.catchment, site.pour]) {
      if (!layer) continue;
      if (selected && !group.hasLayer(layer)) group.addLayer(layer);
      if (!selected && group.hasLayer(layer)) group.removeLayer(layer);
    }
    if (selected) updateLegend({ hasCatchments: !!site.catchment });
    site.pond.setPopupContent(
      candidatePopup(site.feature.properties, site.catchmentData, designStormMm.current)
    );
  }

  function allResultBounds() {
    const bounds = L.latLngBounds([]);
    for (const site of Object.values(sites.current)) {
      bounds.extend(site.pond.getBounds());
    }
    if (regionLayer.current) bounds.extend(regionLayer.current.getBounds());
    return bounds;
  }

  useImperativeHandle(ref, () => ({
    // [[south, west], [north, east]]. Capped at z15 — the finest the elevation data goes, and close
    // enough to see individual fields — so a tiny search hit doesn't zoom to rooftop level.
    flyToBounds(bounds) {
      moveMap(() =>
        mapInstance.current.flyToBounds(bounds, { maxZoom: 15, padding: [40, 40], duration: 1.2 })
      );
    },

    clearRegion() {
      removeRegionLayer();
      emitRegion();
    },

    clearResults() {
      for (const group of Object.values(groups.current)) group.clearLayers();
      sites.current = {};
      selectedRank.current = null;
      mapInstance.current.closePopup();
      updateLegend({
        hasSites: false,
        hasCatchments: false,
        hasExcluded: false,
        hasBuildings: false,
        contours: null,
      });
    },

    // `body` is a /api/contours response; `style` is "lines" or "bands". null clears.
    setContours(body, style) {
      const group = groups.current.contours;
      group.clearLayers();
      if (!body || !body.features.length) {
        updateLegend({ contours: null });
        return;
      }
      const { min, max } = body.elevation_range;

      if (style === "bands") {
        L.geoJSON(body, {
          pane: "contours",
          interactive: false,
          style: (feature) => {
            const t = max > min ? (feature.properties.elevation - min) / (max - min) : 0.5;
            const color = elevationColor(t);
            return { fillColor: color, fillOpacity: 0.45, color, weight: 0.5 };
          },
        }).addTo(group);
        updateLegend({ contours: { style, range: { min, max } } });
        return;
      }

      L.geoJSON(body, {
        pane: "contours",
        interactive: false,
        style: (feature) => ({
          color: CONTOUR_LINE_COLOR,
          weight: feature.properties.major ? 2 : 1,
          opacity: feature.properties.major ? 0.85 : 0.45,
        }),
      }).addTo(group);

      // One label per sufficiently long major line, at its middle vertex — the survey-map
      // convention, without pulling in a text-along-path dependency.
      for (const feature of body.features) {
        if (!feature.properties.major) continue;
        for (const line of feature.geometry.coordinates) {
          if (line.length < 12) continue;
          const [lon, lat] = line[Math.floor(line.length / 2)];
          L.marker([lat, lon], {
            pane: "contours",
            interactive: false,
            keyboard: false,
            icon: L.divIcon({
              className: "contour-label",
              html: `${Math.round(feature.properties.elevation)} m`,
              iconSize: null,
            }),
          }).addTo(group);
        }
      }
      updateLegend({
        contours: {
          style,
          interval: body.interval,
          majorInterval: body.major_interval,
          range: { min, max },
        },
      });
    },

    // `body` is the (region-clipped) /api/candidates FeatureCollection.
    setCandidates(body) {
      groups.current.ponds.clearLayers();
      groups.current.catchments.clearLayers();
      sites.current = {};
      selectedRank.current = null;
      designStormMm.current = body?.summary?.design_storm_mm ?? null;
      if (!body || !body.features.length) {
        updateLegend({ hasSites: false, hasCatchments: false });
        return;
      }

      for (const feature of body.features) {
        const { rank, centroid } = feature.properties;
        const pond = L.geoJSON(feature, {
          pane: "ponds",
          style: () => pondStyle(false),
        });
        pond.bindPopup(candidatePopup(feature.properties, null, designStormMm.current), {
          maxWidth: 320,
          minWidth: 270,
          // Long drainage warnings scroll inside the popup instead of pushing it off-screen.
          maxHeight: 420,
          autoPanPadding: [40, 40],
        });
        pond.on("click", () => onSelectSiteRef.current?.(rank));
        // Closing the details closes the selection too, taking its catchment off the map. (When
        // another pond is picked instead, the selection has already moved on and this is a no-op.)
        pond.on("popupclose", () => {
          if (selectedRank.current === rank) onSelectSiteRef.current?.(null);
        });
        pond.addTo(groups.current.ponds);

        const center = centroid ? [centroid.lat, centroid.lon] : pond.getBounds().getCenter();
        const marker = L.marker(center, {
          icon: rankMarkerIcon(feature.properties, false),
          zIndexOffset: -rank,
          title: `Pond #${rank} — click for details`,
          riseOnHover: true,
        });
        marker.on("click", () => onSelectSiteRef.current?.(rank));
        marker.addTo(groups.current.ponds);

        sites.current[rank] = { feature, pond, marker, catchment: null, pour: null, catchmentData: null };
      }
      updateLegend({ hasSites: true });
    },

    // `results` is /api/catchment's `results`, each carrying the `rank` it was requested with.
    setCatchments(results) {
      const group = groups.current.catchments;
      group.clearLayers();
      for (const result of results) {
        const site = sites.current[result.rank];
        if (!site) continue;
        site.catchmentData = result;
        site.catchment = null;
        site.pour = null;
        if (!result.error && result.geometry) {
          // Built now, shown only while this pond is selected (refreshSite).
          site.catchment = L.geoJSON(result.geometry, {
            pane: "catchments",
            interactive: false,
            style: () => CATCHMENT_STYLE,
          }).bindTooltip(
            `Catchment of pond #${result.rank} · ${fmt.ha(result.area_ha)} (${fmt.km2(result.area_ha)})<br>` +
              `<span>Rain falling here drains into pond #${result.rank}</span>`,
            { permanent: true, direction: "center", className: "catchment-label", interactive: false }
          );
          const { lat, lon } = result.pour_point;
          site.pour = L.circleMarker([lat, lon], {
            pane: "catchments",
            radius: 5,
            color: "#ffffff",
            weight: 2,
            fillColor: CATCHMENT_COLOR,
            fillOpacity: 1,
          }).bindTooltip(`Water enters pond #${result.rank} here`, { direction: "top" });
        }
        refreshSite(result.rank);
      }
    },

    // Marks every site's catchment as failed, so popups stop saying "Delineating…" forever.
    setCatchmentError(message) {
      for (const rank of Object.keys(sites.current)) {
        sites.current[rank].catchmentData = { error: message };
        refreshSite(Number(rank));
      }
    },

    // `excluded` is /api/candidates' `excluded` list (geometry + reason per rejected hollow).
    setExcluded(excluded) {
      const group = groups.current.excluded;
      group.clearLayers();
      const withGeometry = (excluded || []).filter((e) => e.geometry);
      for (const zone of withGeometry) {
        L.geoJSON(zone.geometry, {
          pane: "excluded",
          style: () => ({
            color: "#e6ebe8",
            weight: 2,
            dashArray: "4 4",
            fillColor: "#9aa59f",
            fillOpacity: 0.35,
          }),
        })
          .bindTooltip(`Rejected: ${escapeHtml(zone.reason)}`, { sticky: true, className: "excluded-tip" })
          .addTo(group);
      }
      updateLegend({ hasExcluded: withGeometry.length > 0 && mapInstance.current.hasLayer(group) });
    },

    // Rejected hollows are the whole story when nothing survived, but clutter when five good sites
    // did — so they are shown by default only in the first case. The layer control still toggles
    // them either way.
    showExcluded(visible) {
      const map = mapInstance.current;
      const group = groups.current.excluded;
      if (visible && !map.hasLayer(group)) map.addLayer(group);
      if (!visible && map.hasLayer(group)) map.removeLayer(group);
      updateLegend({ hasExcluded: visible && group.getLayers().length > 0 });
    },

    setBuildings(body) {
      const group = groups.current.buildings;
      group.clearLayers();
      const has = !!(body && body.features && body.features.length);
      if (has) {
        L.geoJSON(body, {
          pane: "ponds",
          style: { color: "#ff3b30", weight: 1, fillColor: "#ff3b30", fillOpacity: 0.25 },
          interactive: false,
        }).addTo(group);
      }
      updateLegend({ hasBuildings: has });
    },

    // Selected site: highlighted pond and catchment, popup open. null deselects.
    setSelected(rank, { open = true, fly = false } = {}) {
      const previous = selectedRank.current;
      selectedRank.current = rank;
      if (previous != null) refreshSite(previous);
      if (rank == null) {
        updateLegend({ hasCatchments: false });
        return;
      }
      refreshSite(rank);
      const site = sites.current[rank];
      if (!site) return;
      const openPopup = () => {
        if (open && selectedRank.current === rank) site.pond.openPopup(site.marker.getLatLng());
      };
      if (fly) {
        const bounds = site.pond.getBounds();
        if (site.catchment) bounds.extend(site.catchment.getBounds());
        mapInstance.current.closePopup();
        // Open only once the flight lands: a popup opened mid-flight can't auto-pan into view and
        // ends up clipped off the top of the map.
        mapInstance.current.once("moveend", openPopup);
        moveMap(() =>
          mapInstance.current.flyToBounds(bounds, { padding: [60, 60], maxZoom: 16, duration: 0.8 })
        );
      } else {
        openPopup();
      }
    },

    // Frames region + sites (+ catchments once they exist). `onlyIfUntouched` skips the move when
    // the user has panned/zoomed since the last fit — their view wins over ours.
    fitToResults({ onlyIfUntouched = false } = {}) {
      if (onlyIfUntouched && userMovedSinceFit.current) return;
      const bounds = allResultBounds();
      if (!bounds.isValid()) return;
      userMovedSinceFit.current = false;
      moveMap(() => mapInstance.current.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 }));
    },
  }));

  return <div id="map" ref={mapEl} />;
});

export default MapView;
