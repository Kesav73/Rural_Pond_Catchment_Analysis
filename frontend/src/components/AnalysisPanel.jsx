export default function AnalysisPanel({
  contoursEnabled,
  candidatesEnabled,
  contoursLoading,
  candidatesLoading,
  onShowContours,
  onFindPondSites,
}) {
  return (
    <section className="panel">
      <h2>
        <span className="step-num">2</span>Analyze terrain
      </h2>
      <p className="panel-hint">
        Runs on the region you drew above — not on whatever the map happens to be showing.
      </p>
      <div className="btn-row">
        <button
          className="btn btn-outline"
          disabled={!contoursEnabled || contoursLoading}
          onClick={onShowContours}
        >
          Show Contours
        </button>
        <button
          className="btn btn-primary"
          disabled={!candidatesEnabled || candidatesLoading}
          onClick={onFindPondSites}
        >
          Find Pond Sites
        </button>
      </div>
    </section>
  );
}
