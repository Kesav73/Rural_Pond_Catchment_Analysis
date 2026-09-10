import { rankColor } from "../colors";

// Sizing (capacity/runoff/capture) already arrives with the initial /api/candidates response
// (Phase 6 moved it upstream of ranking), so this list is populated immediately — it doesn't need
// to wait on the slower /api/catchment call the way the popup's warnings block does.
export default function ResultsPanel({ features, onSelect }) {
  return (
    <section className="panel">
      <h2>
        <span className="step-num">3</span>Ranked sites
      </h2>
      <div id="results-list">
        {features.map((feature, index) => {
          const p = feature.properties;
          const stats = [`${p.area_ha.toFixed(2)} ha pond`];
          if (p.catchment_area_m2 != null) {
            stats.push(`${(p.catchment_area_m2 / 10000).toFixed(1)} ha catchment`);
          }
          if (p.capacity_m3 != null) {
            stats.push(`${Math.round(p.capacity_m3).toLocaleString()} m³ capacity`);
          }
          if (p.capture_fraction != null) {
            stats.push(`${(p.capture_fraction * 100).toFixed(0)}% captured`);
          }
          return (
            <div
              key={index}
              className="result-card"
              style={{ "--rank-color": rankColor(p.rank) }}
              onClick={() => onSelect(index)}
            >
              <div className="result-rank">{p.rank}</div>
              <div className="result-body">
                <div className="result-title">Site #{p.rank}</div>
                <div className="result-stats">
                  {stats.map((s, i) => (
                    <span key={i}>{s}</span>
                  ))}
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
