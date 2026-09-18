import { useCallback, useRef, useState } from "react";
import Brand from "./components/Brand";
import RegionDrawPanel from "./components/RegionDrawPanel";
import AnalysisPanel from "./components/AnalysisPanel";
import ProgressSteps from "./components/ProgressSteps";
import DataWarningBanner from "./components/DataWarningBanner";
import NoSitesPanel from "./components/NoSitesPanel";
import ErrorPanel from "./components/ErrorPanel";
import ResultsPanel from "./components/ResultsPanel";
import MapView from "./components/MapView";
import { pointInPolygon } from "./geo";
import { downloadResultsGeoJSON } from "./exportResults";
import * as api from "./api";

const TOP_N = 5;
const CONTOUR_INTERVAL_M = 2;

// Steps a run moves through, in the order the sidebar lists them.
const IDLE_STEPS = { sites: "pending", catchments: "pending", contours: "pending" };

export default function App() {
  const mapRef = useRef(null);
  const [region, setRegion] = useState(null); // { bbox, polygon, areaKm2, overLimit }
  const [contourMode, setContourMode] = useState("lines"); // "lines" | "bands" | "off"

  // Everything below describes the latest run for the current region; a region change resets it.
  const [running, setRunning] = useState(false);
  const [steps, setSteps] = useState(null); // null until the first run
  const [result, setResult] = useState(null); // { features, summary, outsideShape }
  const [catchments, setCatchments] = useState({}); // rank -> /api/catchment result
  const [error, setError] = useState(null);
  const [selectedRank, setSelectedRank] = useState(null);

  // One "run" per region. Every request belongs to the run that started it; changing or clearing
  // the region aborts that run, so a slow response can never draw results for a shape that is no
  // longer the selection.
  const runRef = useRef({ id: 0, controller: new AbortController() });
  const cancelRun = useCallback(() => {
    runRef.current.controller.abort();
    runRef.current = { id: runRef.current.id + 1, controller: new AbortController() };
  }, []);
  const isStale = (run) => run.id !== runRef.current.id;
  const isAbort = (err) => err && err.name === "AbortError";
  const markStep = (run, name, state) => {
    // Before any run there is no step list to update (e.g. toggling contours on a fresh region).
    if (!isStale(run)) setSteps((prev) => (prev ? { ...prev, [name]: state } : prev));
  };

  const resetResults = useCallback(() => {
    mapRef.current?.clearResults();
    setRunning(false);
    setSteps(null);
    setResult(null);
    setCatchments({});
    setError(null);
    setSelectedRank(null);
  }, []);

  // Any change to the region invalidates whatever is drawn for the previous one — results that no
  // longer correspond to the selected area are worse than no results.
  const handleRegionChange = useCallback(
    (next) => {
      cancelRun();
      resetResults();
      setRegion(next);
    },
    [cancelRun, resetResults]
  );

  const analysisEnabled = region != null && !region.overLimit;

  // Drops anything whose centroid falls outside a freehand polygon. The backend works on the
  // polygon's bounding box, so without this the results would quietly cover more ground than the
  // selection does.
  function insideRegion(items, centroidOf) {
    if (!region.polygon) return { kept: items, dropped: 0 };
    const kept = items.filter((item) => {
      const c = centroidOf(item);
      return c ? pointInPolygon([c.lon, c.lat], region.polygon) : true;
    });
    return { kept, dropped: items.length - kept.length };
  }

  // Contour requests can overlap within one run (the mode switched while one is in flight, or a
  // run starting while a mode change is loading). Only the latest may draw: each request aborts
  // the one before it, so a slow "Shaded" response can't land on top of the "Lines" the user
  // picked after it.
  const contourRequest = useRef(null);

  async function loadContours(mode, run) {
    contourRequest.current?.abort();
    const controller = new AbortController();
    contourRequest.current = controller;
    run.controller.signal.addEventListener("abort", () => controller.abort(), { once: true });
    const isCurrent = () => !isStale(run) && contourRequest.current === controller;

    if (mode === "off") {
      mapRef.current.setContours(null);
      markStep(run, "contours", "skipped");
      return;
    }
    markStep(run, "contours", "active");
    try {
      const body = await api.fetchContours(region.bbox.join(","), CONTOUR_INTERVAL_M, {
        style: mode,
        signal: controller.signal,
      });
      if (!isCurrent()) return;
      mapRef.current.setContours(body, mode);
      markStep(run, "contours", "done");
    } catch (err) {
      if (isAbort(err) || !isCurrent()) return;
      markStep(run, "contours", "error");
    }
  }

  function handleContourMode(mode) {
    setContourMode(mode);
    // Contours belong to a region; with none drawn yet the choice just applies to the next run.
    if (analysisEnabled) loadContours(mode, runRef.current);
  }

  async function handleFindPondSites() {
    // A re-run of the same region starts a fresh run too, so its responses can't mix with the
    // previous run's.
    cancelRun();
    const run = runRef.current;
    const { signal } = run.controller;
    const bbox = region.bbox.join(",");

    mapRef.current.clearResults();
    setResult(null);
    setCatchments({});
    setError(null);
    setSelectedRank(null);
    setRunning(true);
    setSteps({ ...IDLE_STEPS, sites: "active" });

    // Independent of the site search, so it runs alongside it rather than after it.
    loadContours(contourMode, run);

    let features;
    try {
      const body = await api.fetchCandidates(bbox, TOP_N, { signal });
      if (isStale(run)) return;

      const sites = insideRegion(body.features, (f) => f.properties.centroid);
      const rejected = insideRegion(body.excluded || [], (e) => e.centroid);
      features = sites.kept;

      mapRef.current.setCandidates({ ...body, features });
      mapRef.current.setExcluded(rejected.kept);
      mapRef.current.showExcluded(features.length === 0);
      mapRef.current.fitToResults();
      setResult({ features, summary: body.summary, outsideShape: sites.dropped });
      markStep(run, "sites", "done");
    } catch (err) {
      if (isAbort(err) || isStale(run)) return;
      markStep(run, "sites", "error");
      markStep(run, "catchments", "skipped");
      setError(err.message);
      setRunning(false);
      return;
    }

    if (features.length === 0) {
      markStep(run, "catchments", "skipped");
      setRunning(false);
      return;
    }

    // Buildings are an optional warning layer; never let them hold anything else up.
    api
      .fetchBuildings(bbox, { signal })
      .then((b) => {
        if (!isStale(run)) mapRef.current.setBuildings(b);
      })
      .catch(() => {
        /* absence is already reported via summary.overpass_available */
      });

    markStep(run, "catchments", "active");
    try {
      const res = await api.fetchCatchments(
        bbox,
        features.map((f) => f.geometry),
        features.map((f) => f.properties.rank),
        { signal }
      );
      if (isStale(run)) return;
      mapRef.current.setCatchments(res.results);
      setCatchments(Object.fromEntries(res.results.map((r) => [r.rank, r])));
      markStep(run, "catchments", "done");
    } catch (err) {
      if (isAbort(err) || isStale(run)) return;
      // Sites stay usable without outlines; say so rather than leaving popups "delineating".
      mapRef.current.setCatchmentError(err.message);
      markStep(run, "catchments", "error");
    } finally {
      if (!isStale(run)) setRunning(false);
    }
  }

  // From the map (already in view) or from a card (fly there).
  const handleSelect = useCallback((rank, { fly = false } = {}) => {
    setSelectedRank(rank);
    mapRef.current?.setSelected(rank, { fly });
  }, []);

  const handleMapSelect = useCallback((rank) => handleSelect(rank), [handleSelect]);

  const features = result?.features ?? [];
  const hasSites = features.length > 0;

  return (
    <div id="app">
      <aside id="sidebar">
        <Brand />
        <RegionDrawPanel
          region={region}
          onClear={() => mapRef.current?.clearRegion()}
          onPlacePick={(place) => mapRef.current?.flyToBounds(place.bounds)}
        />
        <AnalysisPanel
          enabled={analysisEnabled}
          running={running}
          contourMode={contourMode}
          onContourMode={handleContourMode}
          onRun={handleFindPondSites}
        />
        {steps && <ProgressSteps steps={steps} contourMode={contourMode} />}
        {error && <ErrorPanel message={error} onRetry={handleFindPondSites} />}
        {result && <DataWarningBanner summary={result.summary} />}
        {result && !hasSites && (
          <NoSitesPanel summary={result.summary} outsideShape={result.outsideShape} />
        )}
        {(hasSites || (running && steps?.sites === "active")) && (
          <ResultsPanel
            loading={!result}
            features={features}
            summary={result?.summary}
            selectedRank={selectedRank}
            onSelect={(rank) => handleSelect(rank, { fly: true })}
            onExport={() => downloadResultsGeoJSON(features, catchments, result.summary)}
          />
        )}
        <footer className="sidebar-footer">
          Terrain-derived and water-screened suggestions only — not a check of land ownership. A
          shortlist to visit, not an approval.
        </footer>
      </aside>
      <MapView ref={mapRef} onRegionChange={handleRegionChange} onSelectSite={handleMapSelect} />
    </div>
  );
}
