import { useCallback, useRef, useState } from "react";
import Brand from "./components/Brand";
import RegionDrawPanel from "./components/RegionDrawPanel";
import AnalysisPanel from "./components/AnalysisPanel";
import StatusBar from "./components/StatusBar";
import ResultsPanel from "./components/ResultsPanel";
import MapView from "./components/MapView";
import { pointInPolygon } from "./geo";
import * as api from "./api";

export default function App() {
  const mapRef = useRef(null);
  const [status, setStatusState] = useState(null); // { text, tone, busy }
  const [region, setRegion] = useState(null); // { bbox, polygon, areaKm2, overLimit }
  const [contoursLoading, setContoursLoading] = useState(false);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [candidatesData, setCandidatesData] = useState(null); // FeatureCollection, drives ResultsPanel

  // No explicit tone hint at each call site — a light heuristic on the text keeps every message
  // below readable as one of three states without threading a tone argument through everything.
  const setStatus = useCallback((text, opts = {}) => {
    const busy = !!opts.busy;
    const tone = busy
      ? "info"
      : /fail|too large/i.test(text)
        ? "error"
        : /^(loaded|top \d|no suitable|no sites)/i.test(text)
          ? "success"
          : "info";
    setStatusState({ text, tone, busy });
  }, []);

  function appendStatus(suffix) {
    setStatusState((prev) => (prev ? { ...prev, text: prev.text + suffix } : prev));
  }

  // Any change to the region invalidates whatever is drawn for the previous one — results that no
  // longer correspond to the selected area are worse than no results.
  const handleRegionChange = useCallback(
    (next) => {
      setRegion(next);
      mapRef.current?.setContours(null);
      mapRef.current?.setCandidates(null);
      setCandidatesData(null);

      if (!next) {
        setStatus("Region cleared — draw an area on the map to analyze.");
      } else if (next.overLimit) {
        setStatus(
          `Area too large (≈ ${next.areaKm2.toFixed(0)} km²) — draw something closer to the size ` +
            `of a village, not a district.`
        );
      } else {
        setStatus(`Region selected: ≈ ${next.areaKm2.toFixed(1)} km². Ready to analyze.`);
      }
    },
    [setStatus]
  );

  const analysisEnabled = region != null && !region.overLimit;

  async function handleShowContours() {
    const bbox = region.bbox.join(",");
    setStatus("Loading contours…", { busy: true });
    setContoursLoading(true);
    try {
      const body = await api.fetchContours(bbox, 2);
      mapRef.current.setContours(body);
      const { min, max } = body.elevation_range;
      setStatus(`Loaded ${body.features.length} contour bands (${min.toFixed(0)}–${max.toFixed(0)}m)`);
    } catch (err) {
      setStatus(`Failed to load contours: ${err.message}`);
    } finally {
      setContoursLoading(false);
    }
  }

  async function handleFindPondSites() {
    const bbox = region.bbox.join(",");
    setStatus("Finding pond sites…", { busy: true });
    setCandidatesLoading(true);
    try {
      const body = await api.fetchCandidates(bbox, 5);
      mapRef.current.setCandidates(null);
      setCandidatesData(null);
      const summary = body.summary;

      // The DEM fetch is bbox-based, so a freehand polygon region still gets analysed across its
      // bounding box. Drop anything whose centroid falls outside the shape the user actually drew,
      // otherwise the results quietly cover more ground than the selection does.
      let features = body.features;
      let outsideShape = 0;
      if (region.polygon) {
        const before = features.length;
        features = features.filter((feature) => {
          const centroid = feature.properties.centroid;
          return centroid ? pointInPolygon([centroid.lon, centroid.lat], region.polygon) : true;
        });
        outsideShape = before - features.length;
      }
      const clipped = { ...body, features };

      if (features.length === 0) {
        const clipNote = outsideShape
          ? ` ${outsideShape} site(s) were found inside the bounding box but outside your drawn shape.`
          : "";
        setStatus(
          `No suitable sites in this area — ${summary.zones_in_view} depressions checked, all ` +
            `filtered out (${summary.excluded_water} on/near existing water, ` +
            `${summary.excluded_shape} drainage-like).${clipNote}`
        );
        return;
      }

      mapRef.current.setCandidates(clipped);
      setCandidatesData(clipped);

      // Deliberately not awaited: buildings are an optional warning layer, and awaiting it when
      // Overpass is slow/unreachable would leave the results invisible behind the status line for
      // ~20s even though the candidates were already drawn.
      api
        .fetchBuildings(bbox)
        .then((b) => mapRef.current.setBuildings(b))
        .catch(() => {});

      // Be explicit when a source was unavailable rather than implying a full screen ran.
      const degraded = [];
      if (!summary.worldcover_available) degraded.push("WorldCover");
      if (!summary.swir_available) degraded.push("SWIR");
      if (!summary.overpass_available) degraded.push("OSM");
      if (!summary.rainfall_available) degraded.push("rainfall");
      const caveat = degraded.length ? ` — unavailable: ${degraded.join(", ")}` : "";
      const clipNote = outsideShape ? ` · ${outsideShape} outside your drawn shape` : "";

      setStatus(
        `Top ${features.length} of ${summary.eligible} sites ` +
          `(${summary.excluded_water} on/near existing water, ${summary.excluded_shape} drainage-like, excluded) ` +
          `· ${summary.design_storm_mm.toFixed(0)} mm design storm${clipNote}${caveat}`
      );

      // Background, same reasoning as buildings above — the ~10s flow solve shouldn't hide
      // already-computed results.
      api
        .fetchCatchments(bbox, features.map((f) => f.geometry))
        .then((res) => {
          for (const result of res.results) mapRef.current.setCatchment(result.index, result);
          appendStatus(" · catchments ready — click a site");
        })
        .catch(() => {
          /* soft-fail: candidates remain usable without catchment figures */
        });
    } catch (err) {
      setStatus(`Failed to find pond sites: ${err.message}`);
    } finally {
      setCandidatesLoading(false);
    }
  }

  function handleResultClick(index) {
    mapRef.current?.flyToCandidate(index);
  }

  return (
    <div id="app">
      <aside id="sidebar">
        <Brand />
        <RegionDrawPanel region={region} onClear={() => mapRef.current?.clearRegion()} />
        <AnalysisPanel
          contoursEnabled={analysisEnabled}
          candidatesEnabled={analysisEnabled}
          contoursLoading={contoursLoading}
          candidatesLoading={candidatesLoading}
          onShowContours={handleShowContours}
          onFindPondSites={handleFindPondSites}
        />
        {status && <StatusBar {...status} />}
        {candidatesData && candidatesData.features.length > 0 && (
          <ResultsPanel features={candidatesData.features} onSelect={handleResultClick} />
        )}
        <footer className="sidebar-footer">
          Terrain-derived and water-screened suggestions only — not a check of land ownership. A
          shortlist to visit, not an approval.
        </footer>
      </aside>
      <MapView ref={mapRef} onRegionChange={handleRegionChange} />
    </div>
  );
}
