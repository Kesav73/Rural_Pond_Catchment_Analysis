// What the run is doing, step by step. The site search is one request, so it shows as a single
// active step rather than pretending to know how far through it the server is.
const LABELS = {
  sites: {
    active: "Reading terrain, screening out existing water, ranking sites…",
    done: "Pond sites found",
    error: "Site search failed",
  },
  catchments: {
    pending: "Catchments",
    active: "Tracing the land that drains into each site…",
    done: "Catchments traced",
    error: "Catchment outlines failed — sites are still usable",
    skipped: "Catchments — skipped",
  },
  contours: {
    pending: "Contours",
    active: "Drawing contours…",
    done: "Contours drawn",
    error: "Contours failed to load",
    skipped: "Contours off",
  },
};

const ICONS = { pending: "", active: "", done: "✓", error: "!", skipped: "–" };

export default function ProgressSteps({ steps }) {
  const order = ["sites", "catchments", "contours"];
  const allSettled = order.every((name) => steps[name] !== "active" && steps[name] !== "pending");
  // Once everything has finished cleanly the list has nothing left to say.
  if (allSettled && order.every((name) => steps[name] !== "error")) return null;

  return (
    <ol className="progress-steps" aria-live="polite">
      {order.map((name) => {
        const state = steps[name];
        const label = LABELS[name][state] ?? LABELS[name].pending ?? name;
        return (
          <li key={name} className={`step step-${state}`}>
            <span className="step-icon">{state === "active" ? <span className="spinner" /> : ICONS[state]}</span>
            <span>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}
