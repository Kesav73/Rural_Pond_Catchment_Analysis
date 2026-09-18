import PlaceSearch from "./PlaceSearch";

export default function RegionDrawPanel({ region, onClear, onPlacePick }) {
  return (
    <section className="panel">
      <h2>
        <span className="step-num">1</span>Draw the area to analyze
      </h2>

      <PlaceSearch onPick={onPlacePick} />

      {!region ? (
        <p className="panel-hint">
          Find your area (search above, or scroll to zoom and drag to pan), then use the rectangle or
          polygon tool on the map (top-left) to draw the boundary you want analyzed.
        </p>
      ) : (
        <>
          <div className={`region-readout${region.overLimit ? " region-readout-warn" : ""}`}>
            <span className="region-area">
              <span className="region-label">Search area</span>≈ {region.areaKm2.toFixed(1)} km²
            </span>
            <span className="region-shape">{region.polygon ? "polygon" : "rectangle"}</span>
          </div>

          {region.overLimit && (
            <p className="region-warning">
              Area too large to analyze — draw something closer to the size of a village, not a
              district.
            </p>
          )}

          <button className="btn btn-outline" onClick={onClear}>
            Clear region
          </button>
        </>
      )}
    </section>
  );
}
