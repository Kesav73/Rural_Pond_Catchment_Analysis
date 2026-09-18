import { useEffect, useRef } from "react";
import { rankColor } from "../colors";
import * as fmt from "../format";

function SkeletonRows() {
  return (
    <div className="result-list" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <div key={i} className="result-row is-skeleton">
          <div className="skeleton skeleton-badge" />
          <div className="result-body">
            <div className="skeleton skeleton-line" />
            <div className="skeleton skeleton-line short" />
          </div>
        </div>
      ))}
    </div>
  );
}

// The ranking only: which pond, in what order, and the one number that decided the order. Everything
// else about a pond — its catchment, capacity, depth, warnings — is one click away, in its details.
export default function ResultsPanel({ loading, features, summary, selectedRank, onSelect, onExport }) {
  const rowRefs = useRef({});

  // A pond picked on the map should be findable in the list without hunting for it.
  useEffect(() => {
    if (selectedRank != null) {
      rowRefs.current[selectedRank]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [selectedRank]);

  return (
    <section className="panel">
      <h2>
        <span className="step-num">3</span>Best pond locations
      </h2>

      {loading ? (
        <SkeletonRows />
      ) : (
        <>
          <p className="panel-hint">
            Top {features.length} of {summary.eligible} suitable locations, best first. Click a pond
            — here or on the map — to see its catchment and details.
          </p>

          <div className="result-list">
            {features.map((feature) => {
              const p = feature.properties;
              const volume = fmt.expectedVolume(p);
              const selected = selectedRank === p.rank;
              return (
                <button
                  type="button"
                  key={p.rank}
                  ref={(el) => (rowRefs.current[p.rank] = el)}
                  className={`result-row${selected ? " is-selected" : ""}`}
                  style={{ "--rank-color": rankColor(p.rank) }}
                  onClick={() => onSelect(p.rank)}
                  aria-pressed={selected}
                >
                  <span className="result-rank">{p.rank}</span>
                  <span className="result-body">
                    <span className="result-title">Pond #{p.rank}</span>
                    {volume != null && (
                      <span className="result-sub">{fmt.m3(volume)} of water per heavy storm</span>
                    )}
                  </span>
                  <span className="result-chevron" aria-hidden="true">
                    ›
                  </span>
                </button>
              );
            })}
          </div>

          <button type="button" className="btn btn-outline btn-block" onClick={onExport}>
            Download results (GeoJSON)
          </button>
        </>
      )}
    </section>
  );
}
