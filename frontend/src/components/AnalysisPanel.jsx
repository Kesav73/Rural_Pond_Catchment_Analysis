const CONTOUR_MODES = [
  { value: "lines", label: "Lines" },
  { value: "bands", label: "Shaded" },
  { value: "off", label: "Off" },
];

export default function AnalysisPanel({ enabled, running, contourMode, onContourMode, onRun }) {
  return (
    <section className="panel">
      <h2>
        <span className="step-num">2</span>Analyze terrain
      </h2>
      <p className="panel-hint">
        Finds the best pond sites inside the area you drew, with the land that drains into each and
        the water it can collect.
      </p>

      <div className="field-row">
        <span className="field-label" id="contour-mode-label">
          Contours
        </span>
        <div className="segmented" role="radiogroup" aria-labelledby="contour-mode-label">
          {CONTOUR_MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              role="radio"
              aria-checked={contourMode === mode.value}
              className={contourMode === mode.value ? "is-active" : ""}
              onClick={() => onContourMode(mode.value)}
            >
              {mode.label}
            </button>
          ))}
        </div>
      </div>

      <button className="btn btn-primary btn-block" disabled={!enabled || running} onClick={onRun}>
        {running ? (
          <>
            <span className="spinner" /> Analyzing…
          </>
        ) : (
          "Find Pond Sites"
        )}
      </button>
      {!enabled && !running && (
        <p className="panel-hint panel-hint-tight">Draw an area on the map first.</p>
      )}
    </section>
  );
}
